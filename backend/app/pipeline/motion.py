"""Stage 5, motion: `signals/motion.json` = mean absolute difference between consecutive
grayscale frames sampled at 1 fps and 160 px wide, streamed from ffmpeg (docs/04).

Long videos decode only keyframes (D-033): the decoder skips every frame in between, which is
where nearly all the decoding time goes. ffmpeg's fps filter then repeats each keyframe until
the next one, so the change between two keyframes arrives as one value after a run of zeros;
`spread_repeats` shares it over that run to keep a per-second rate. The fps filter drops the
very last keyframe (it has no known length), so up to one keyframe gap at the end is padded.
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np
import numpy.typing as npt

from app.config import (
    MOTION_FPS,
    MOTION_KEYFRAME_MAX_GAP_S,
    MOTION_KEYFRAME_MIN_DURATION_S,
    MOTION_KEYFRAME_PROBE_S,
    MOTION_WIDTH,
    PROBE_TIMEOUT_S,
    SIGNAL_TIMEOUT_BASE_S,
    SIGNAL_TIMEOUT_PER_VIDEO_S,
)
from app.core.hardware import MemoryInfo, ffmpeg_thread_args, find_ffprobe, require_ffmpeg
from app.core.proc import CommandError, StreamingCommand, run_command
from app.pipeline.scoring import fit_length
from app.pipeline.signals import SignalFile, grid_length, load_signal, save_signal
from app.sources.base import SOURCE_FILE_NAME

logger = logging.getLogger(__name__)


def analysis_height(width: int, height: int) -> int:
    """Height of the 160 px wide analysis frame, kept even and at least 2 px."""
    return max(2, round(MOTION_WIDTH * height / width / 2) * 2)


def keyframe_gap_s(path: Path, ffmpeg_path: str | None) -> float | None:
    """Median seconds between video keyframes in the first `MOTION_KEYFRAME_PROBE_S` seconds,
    read from packet flags (no decoding). None when ffprobe is missing or fails, or when there
    are fewer than two keyframes in that stretch."""
    ffprobe = find_ffprobe(ffmpeg_path)
    if ffprobe is None:
        return None
    args = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-read_intervals", f"%+{MOTION_KEYFRAME_PROBE_S}",
        "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(path),
    ]  # fmt: skip
    try:
        result = run_command(args, PROBE_TIMEOUT_S)
    except CommandError as exc:
        logger.warning("keyframe probe failed: %s", exc)
        return None
    if result.returncode != 0:
        return None
    times: list[float] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 2 or "K" not in parts[1]:
            continue
        try:
            times.append(float(parts[0]))
        except ValueError:
            continue
    if len(times) < 2:
        return None
    return float(np.median(np.diff(sorted(times))))


def keyframe_mode_gap(path: Path, duration_s: float, ffmpeg_path: str | None) -> float | None:
    """The keyframe gap to sample motion with, or None for a full decode: short videos, and
    files whose keyframes are too far apart or could not be read."""
    if duration_s < MOTION_KEYFRAME_MIN_DURATION_S:
        return None
    gap = keyframe_gap_s(path, ffmpeg_path)
    return gap if gap is not None and gap <= MOTION_KEYFRAME_MAX_GAP_S else None


def spread_repeats(values: list[float], max_run: int) -> list[float]:
    """Share each value that follows a run of exact zeros evenly over that run (at most
    `max_run` seconds including itself). Index 0 is never part of a run."""
    result = list(values)
    run = 0
    for index, value in enumerate(values):
        if index > 0 and value == 0.0:
            run += 1
            continue
        if value > 0.0 and run:
            span = min(run, max_run - 1) + 1
            share = value / span
            for position in range(index - span + 1, index + 1):
                result[position] = share
        run = 0
    return result


def motion_series(
    video_dir: Path,
    duration_s: float,
    width: int,
    height: int,
    *,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    keyframe_gap: float | None = None,
    cancel: threading.Event | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> list[float]:
    """Per-second motion values; only two frames are held in memory at a time.
    With `keyframe_gap` (seconds) only keyframes are decoded, and each change is spread over
    at most that many seconds. `on_progress` gets the fraction of the expected frames read.

    Raises:
        AppError: FFMPEG_MISSING.
        CommandError: ffmpeg failed, timed out, or was cancelled.
    """
    ffmpeg = require_ffmpeg(ffmpeg_path)
    frame_h = analysis_height(width, height)
    frame_size = MOTION_WIDTH * frame_h
    skip = ["-skip_frame", "nokey"] if keyframe_gap is not None else []
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
        *ffmpeg_thread_args(memory), *skip,
        "-i", str(video_dir / SOURCE_FILE_NAME), "-an",
        "-vf", f"fps={MOTION_FPS},scale={MOTION_WIDTH}:{frame_h},format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]  # fmt: skip
    timeout = SIGNAL_TIMEOUT_BASE_S + duration_s * SIGNAL_TIMEOUT_PER_VIDEO_S
    values: list[float] = []
    expected = grid_length(duration_s)
    previous: npt.NDArray[np.int16] | None = None
    with StreamingCommand(args, timeout, cancel) as command:
        while True:
            raw = command.read_exact(frame_size)
            if raw is None or len(raw) < frame_size:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).astype(np.int16)
            values.append(0.0 if previous is None else float(np.mean(np.abs(frame - previous))))
            previous = frame
            if on_progress is not None:
                on_progress(min(1.0, len(values) / expected))
    if keyframe_gap is not None:
        values = spread_repeats(values, max(1, math.ceil(keyframe_gap)))
    return [float(v) for v in fit_length(np.asarray(values), grid_length(duration_s))]


def run_motion(
    video_dir: Path,
    duration_s: float,
    width: int,
    height: int,
    *,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    cancel: threading.Event | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> SignalFile:
    """Write `signals/motion.json` (skipped when a valid copy exists)."""
    cached = load_signal(video_dir, "motion", duration_s)
    if cached is not None:
        return cached
    gap = keyframe_mode_gap(video_dir / SOURCE_FILE_NAME, duration_s, ffmpeg_path)
    logger.info("motion keyframe_gap=%s duration=%.0f", gap, duration_s)
    values = motion_series(
        video_dir, duration_s, width, height, ffmpeg_path=ffmpeg_path, memory=memory,
        keyframe_gap=gap, cancel=cancel, on_progress=on_progress,
    )  # fmt: skip
    return save_signal(video_dir, "motion", values)
