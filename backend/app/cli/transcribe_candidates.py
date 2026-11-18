"""Transcribe the candidate spans of an analysed video (after `app.cli.find_candidates`).

Usage (from `backend/`):
    python -m app.cli.transcribe_candidates <folder> [--model auto|tiny|base|small|medium]
        [--thorough] [--hinglish]

Prints the planned spans, the model and device, timing, peak memory, and sample words with
their timestamps.
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

from app.config import load_config
from app.core.errors import AppError
from app.core.hardware import nvidia_vram_gb, pick_whisper_model, read_memory
from app.core.metrics import peak_memory_gb
from app.core.proc import CommandError
from app.pipeline.audio import AUDIO_FILE_NAME
from app.pipeline.candidates import load_cached as load_candidates
from app.pipeline.download import load_cached as load_download
from app.pipeline.transcribe import (
    TRANSCRIPT_FILE_NAME,
    WhisperRunner,
    plan_spans,
    run_transcribe,
)

SAMPLE_WORDS = 12


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--model", default="auto")
    parser.add_argument("--thorough", action="store_true")
    parser.add_argument("--hinglish", action="store_true")
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    folder: Path = args.folder
    config = load_config()
    info = load_download(folder, config.ffmpeg_path)
    candidates = load_candidates(folder)
    if info is None or candidates is None:
        print("ERROR: run app.cli.find_candidates on this folder first", file=sys.stderr)
        return 1
    vram = nvidia_vram_gb()
    model = args.model if args.model != "auto" else pick_whisper_model(read_memory(), vram)
    spans = plan_spans(
        [(c.start, c.end) for c in candidates.items],
        info.probed_duration_s,
        thorough=args.thorough,
    )
    planned = sum(span.length for span in spans)
    print(
        f"model: {model}; spans: {len(spans)} ({planned:.0f} s of {info.probed_duration_s:.0f} s)"
    )
    for span in spans:
        print(f"  {span.start:8.1f} - {span.end:8.1f}")
    last = [-1]

    def show(fraction: float) -> None:
        step = int(fraction * 10)
        if step != last[0]:
            last[0] = step
            print(f"transcribe: {fraction:.0%}", flush=True)

    runners: list[WhisperRunner] = []

    def factory() -> WhisperRunner:
        runner = WhisperRunner(model, config.data_dir / "models", use_gpu=vram is not None)
        runners.append(runner)
        print(f"device: {runner.device}")
        return runner

    started = time.monotonic()
    try:
        transcript = run_transcribe(
            folder, folder / AUDIO_FILE_NAME, spans,
            caption_language="hinglish" if args.hinglish else "en",
            runner_factory=factory, on_progress=show,
        )  # fmt: skip
    except (AppError, CommandError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    runners.clear()
    words = [word for segment in transcript.segments for word in segment.words]
    print(
        f"transcript: {len(transcript.segments)} segments, {len(words)} words, "
        f"language {transcript.language} -> {folder / TRANSCRIPT_FILE_NAME}"
    )
    print(f"time: {time.monotonic() - started:.1f} s; peak memory {peak_memory_gb():.2f} GB")
    for segment in transcript.segments[:3]:
        print(f"  [{segment.start:.2f}-{segment.end:.2f}] {segment.text}")
    for word in words[:SAMPLE_WORDS]:
        print(f"    {word.start:8.2f} {word.end:8.2f} {word.text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
