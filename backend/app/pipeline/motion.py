"""Stage 5, motion: `signals/motion.json` = mean absolute difference between consecutive
grayscale frames sampled at 1 fps and 160 px wide, streamed from ffmpeg (docs/04)."""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import numpy.typing as npt

from app.config import (
    MOTION_FPS,
    MOTION_WIDTH,
    SIGNAL_TIMEOUT_BASE_S,
    SIGNAL_TIMEOUT_PER_VIDEO_S,
)
from app.core.hardware import MemoryInfo, ffmpeg_thread_args, require_ffmpeg
from app.core.proc import StreamingCommand
from app.pipeline.scoring import fit_length
from app.pipeline.signals import SignalFile, grid_length, load_signal, save_signal
from app.sources.base import SOURCE_FILE_NAME


def analysis_height(width: int, height: int) -> int:
    """Height of the 160 px wide analysis frame, kept even and at least 2 px."""
    return max(2, round(MOTION_WIDTH * height / width / 2) * 2)


def motion_series(
    video_dir: Path,
    duration_s: float,
    width: int,
    height: int,
    *,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    cancel: threading.Event | None = None,
) -> list[float]:
    """Per-second motion values; only two frames are held in memory at a time.

    Raises:
        AppError: FFMPEG_MISSING.
        CommandError: ffmpeg failed, timed out, or was cancelled.
    """
    ffmpeg = require_ffmpeg(ffmpeg_path)
    frame_h = analysis_height(width, height)
    frame_size = MOTION_WIDTH * frame_h
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
        *ffmpeg_thread_args(memory),
        "-i", str(video_dir / SOURCE_FILE_NAME), "-an",
        "-vf", f"fps={MOTION_FPS},scale={MOTION_WIDTH}:{frame_h},format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]  # fmt: skip
    timeout = SIGNAL_TIMEOUT_BASE_S + duration_s * SIGNAL_TIMEOUT_PER_VIDEO_S
    values: list[float] = []
    previous: npt.NDArray[np.int16] | None = None
    with StreamingCommand(args, timeout, cancel) as command:
        while True:
            raw = command.read_exact(frame_size)
            if raw is None or len(raw) < frame_size:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).astype(np.int16)
            values.append(0.0 if previous is None else float(np.mean(np.abs(frame - previous))))
            previous = frame
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
) -> SignalFile:
    """Write `signals/motion.json` (skipped when a valid copy exists)."""
    cached = load_signal(video_dir, "motion", duration_s)
    if cached is not None:
        return cached
    values = motion_series(
        video_dir, duration_s, width, height,
        ffmpeg_path=ffmpeg_path, memory=memory, cancel=cancel,
    )  # fmt: skip
    return save_signal(video_dir, "motion", values)
