"""Run the signal stages (audio, motion, chat) and candidate scoring on a downloaded video.

Usage (from `backend/`):
    python -m app.cli.find_candidates <folder>

`<folder>` holds `source.mp4` and `info.json` (and `chat.json` when the video has a chat
replay), e.g. the output of `app.cli.fetch_source`. Prints the candidate windows, stage
durations, and the peak memory of the process.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from app.config import load_config
from app.core.errors import AppError
from app.core.hardware import read_memory
from app.core.metrics import peak_memory_gb
from app.core.proc import CommandError
from app.pipeline.audio import run_audio
from app.pipeline.candidates import CANDIDATES_FILE_NAME, run_candidates
from app.pipeline.chat import load_cached as load_chat
from app.pipeline.download import load_cached as load_download
from app.pipeline.motion import run_motion
from app.pipeline.screen import run_screen
from app.pipeline.signals import SignalFile, run_chat_signal


def _clock(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 3600}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    args = parser.parse_args(argv)
    folder: Path = args.folder
    config = load_config()
    memory = read_memory()
    try:
        info = load_download(folder, config.ffmpeg_path)
        if info is None:
            print("ERROR: no valid source.mp4 + info.json in this folder", file=sys.stderr)
            return 1
        duration = info.probed_duration_s
        print(f"video: {info.video.title} ({duration:.1f} s, {info.width}x{info.height})")
        signals: dict[str, SignalFile] = {}
        started = time.monotonic()
        signals["audio"] = run_audio(
            folder, duration, ffmpeg_path=config.ffmpeg_path, memory=memory
        )
        print(f"audio: {time.monotonic() - started:.1f} s, peak memory {peak_memory_gb():.2f} GB")
        started = time.monotonic()
        signals["motion"] = run_motion(
            folder, duration, info.width, info.height,
            ffmpeg_path=config.ffmpeg_path, memory=memory,
        )  # fmt: skip
        print(f"motion: {time.monotonic() - started:.1f} s, peak memory {peak_memory_gb():.2f} GB")
        started = time.monotonic()
        screen = run_screen(
            folder, duration, config.data_dir / "models",
            ffmpeg_path=config.ffmpeg_path, memory=memory,
        )  # fmt: skip
        print(f"screen: {time.monotonic() - started:.1f} s, {len(screen.shares)} frames")
        chat = load_chat(folder)
        if chat is not None:
            signals["chat"] = run_chat_signal(folder, chat.items, duration)
            print(f"chat: {len(chat.items)} messages")
        else:
            print("chat: none (weights renormalized over audio and motion)")
        result = run_candidates(folder, signals, duration, screen)
    except (AppError, CommandError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"candidates: {len(result.items)} -> {folder / CANDIDATES_FILE_NAME}")
    for number, item in enumerate(result.items, start=1):
        parts = " ".join(
            f"{name}={value:.2f}"
            for name, value in item.signals.model_dump().items()
            if value is not None
        )
        print(
            f"  {number:2d}. peak {_clock(item.peak_t)}  window {_clock(item.start)}-"
            f"{_clock(item.end)}  score {item.score:.3f}  ({parts})"
        )
    print(f"peak memory: {peak_memory_gb():.2f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
