"""Stage 9, cut: `work/<clip>/cut.mp4` with up to 5 s of handle on each side (docs/04).

Re-encoded (not stream copy) for frame-accurate cuts, normalized to 30 fps. `cut.json`
records the source range so a cached cut is reused only for the same range.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.config import RENDER_TIMEOUT_BASE_S, RENDER_TIMEOUT_PER_CLIP_S
from app.core.errors import AppError, ErrorCode
from app.core.files import write_json_atomic
from app.core.hardware import MemoryInfo, ffmpeg_thread_args, require_ffmpeg
from app.core.proc import CommandError, run_command
from app.render import layout_constants as lc

logger = logging.getLogger(__name__)

CUT_FILE_NAME = "cut.mp4"
CUT_INFO_FILE_NAME = "cut.json"
RANGE_TOLERANCE_S = 0.01


class CutInfo(BaseModel):
    """What `cut.mp4` holds: source seconds [source_start, source_end]."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    source_start: float
    source_end: float
    width: int
    height: int


def cut_range(start_s: float, end_s: float, duration_s: float) -> tuple[float, float]:
    """The source range to cut: the clip plus handles, clamped to the video."""
    return (
        max(0.0, start_s - lc.HANDLE_BEFORE_S),
        min(duration_s, end_s + lc.HANDLE_AFTER_S),
    )


def load_cut(work_dir: Path) -> CutInfo | None:
    path = work_dir / CUT_INFO_FILE_NAME
    if not path.is_file() or not (work_dir / CUT_FILE_NAME).is_file():
        return None
    try:
        return CutInfo.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def run_cut(
    source: Path,
    work_dir: Path,
    source_start: float,
    source_end: float,
    src_w: int,
    src_h: int,
    *,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    cancel: threading.Event | None = None,
) -> CutInfo:
    """Cut [source_start, source_end] from `source` into `cut.mp4` (cached for the same range).

    Raises:
        AppError: FFMPEG_MISSING or RENDER_FAILED.
        CommandCancelledError / CommandTimeoutError.
    """
    cached = load_cut(work_dir)
    if (
        cached is not None
        and abs(cached.source_start - source_start) < RANGE_TOLERANCE_S
        and abs(cached.source_end - source_end) < RANGE_TOLERANCE_S
    ):
        return cached
    ffmpeg = require_ffmpeg(ffmpeg_path)
    work_dir.mkdir(parents=True, exist_ok=True)
    length = source_end - source_start
    temp = work_dir / "cut.part.mp4"
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
        *ffmpeg_thread_args(memory),
        "-ss", f"{source_start:.3f}", "-i", str(source), "-t", f"{length:.3f}",
        "-map", "0:v:0", "-map", "0:a:0?", "-vf", f"fps={lc.FPS}",
        "-c:v", "libx264", "-preset", lc.INTERMEDIATE_PRESET, "-crf", str(lc.INTERMEDIATE_CRF),
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", lc.INTERMEDIATE_AUDIO_BITRATE,
        "-ar", "48000", str(temp),
    ]  # fmt: skip
    try:
        result = run_command(
            args, RENDER_TIMEOUT_BASE_S + length * RENDER_TIMEOUT_PER_CLIP_S, cancel
        )
    except CommandError:
        temp.unlink(missing_ok=True)
        raise
    if result.returncode != 0:
        temp.unlink(missing_ok=True)
        logger.error("cut failed: %s", result.stderr[-2000:])
        raise AppError(ErrorCode.RENDER_FAILED, "Cutting the clip from the video failed.")
    temp.replace(work_dir / CUT_FILE_NAME)
    info = CutInfo(source_start=source_start, source_end=source_end, width=src_w, height=src_h)
    write_json_atomic(work_dir / CUT_INFO_FILE_NAME, info)
    return info
