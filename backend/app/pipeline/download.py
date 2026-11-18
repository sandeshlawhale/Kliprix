"""Stage 2, download: `source.mp4` + `info.json` in the video's download folder (docs/04, 08).

The cached output is reused only when `info.json` parses and ffprobe confirms that
`source.mp4` has a video stream and the expected length.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.config import (
    DOWNLOAD_LOW_RAM_GB,
    DOWNLOAD_LOW_RAM_MAX_HEIGHT,
    DOWNLOAD_MAX_HEIGHT,
    PROBE_TIMEOUT_S,
)
from app.core.errors import AppError, ErrorCode
from app.core.files import write_json_atomic
from app.core.hardware import MemoryInfo, find_ffprobe
from app.core.proc import CommandError, run_command
from app.sources.base import SOURCE_FILE_NAME, ProgressCallback, SourceAdapter, VideoMeta

logger = logging.getLogger(__name__)

INFO_FILE_NAME = "info.json"
# A cached file must be at least this fraction of the reported length to count as complete.
MIN_DURATION_RATIO = 0.98


class SourceInfo(BaseModel):
    """`info.json`: what was downloaded, from where, and what ffprobe measured."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    url: str
    video: VideoMeta
    max_height: int
    width: int
    height: int
    probed_duration_s: float


@dataclass(frozen=True)
class ProbeResult:
    duration_s: float
    width: int
    height: int


def max_download_height(memory: MemoryInfo) -> int:
    """720p cap below 6 GB RAM, else 1080p (docs/04 "Download", docs/11)."""
    if memory.total_gb < DOWNLOAD_LOW_RAM_GB:
        return DOWNLOAD_LOW_RAM_MAX_HEIGHT
    return DOWNLOAD_MAX_HEIGHT


def probe_video(path: Path, ffmpeg_path: str | None) -> ProbeResult | None:
    """Duration and size of the first video stream, or None when it is not a playable video.

    Raises:
        AppError FFMPEG_MISSING: ffprobe is not available.
    """
    ffprobe = find_ffprobe(ffmpeg_path)
    if ffprobe is None:
        raise AppError(
            ErrorCode.FFMPEG_MISSING,
            "ffprobe was not found. Install ffmpeg and run the system check again.",
        )
    args = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height:format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = run_command(args, PROBE_TIMEOUT_S)
    except CommandError as exc:
        logger.warning("ffprobe failed file=%s error=%s", path.name, exc)
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        return ProbeResult(
            duration_s=float(data["format"]["duration"]),
            width=int(stream["width"]),
            height=int(stream["height"]),
        )
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
        return None


def load_cached(dest_dir: Path, ffmpeg_path: str | None) -> SourceInfo | None:
    """The cached download if `info.json` and `source.mp4` are both valid, else None."""
    info_path = dest_dir / INFO_FILE_NAME
    source = dest_dir / SOURCE_FILE_NAME
    if not info_path.is_file() or not source.is_file():
        return None
    try:
        info = SourceInfo.model_validate_json(info_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None
    probe = probe_video(source, ffmpeg_path)
    if probe is None or probe.duration_s < info.video.duration_s * MIN_DURATION_RATIO:
        return None
    return info


def run_download(
    adapter: SourceAdapter,
    url: str,
    dest_dir: Path,
    *,
    ffmpeg_path: str | None,
    max_height: int,
    cancel: threading.Event | None = None,
    on_progress: ProgressCallback | None = None,
) -> SourceInfo:
    """Download `url` into `dest_dir` (skipped when a valid cached copy exists).

    Raises:
        AppError: the adapter's codes, FFMPEG_MISSING, or DOWNLOAD_FAILED when the result
            is not a playable video.
        SourceCancelledError: `cancel` was set.
    """
    cached = load_cached(dest_dir, ffmpeg_path)
    if cached is not None:
        logger.info("download cached source_id=%s", cached.video.source_id)
        return cached
    source, meta = adapter.download(
        url, dest_dir, max_height=max_height, cancel=cancel, on_progress=on_progress
    )
    probe = probe_video(source, ffmpeg_path)
    if probe is None:
        raise AppError(ErrorCode.DOWNLOAD_FAILED, "The downloaded file is not a playable video.")
    info = SourceInfo(
        url=url,
        video=meta,
        max_height=max_height,
        width=probe.width,
        height=probe.height,
        probed_duration_s=probe.duration_s,
    )
    write_json_atomic(dest_dir / INFO_FILE_NAME, info)
    logger.info(
        "download done source_id=%s height=%s duration_s=%.1f",
        meta.source_id,
        probe.height,
        probe.duration_s,
    )
    return info
