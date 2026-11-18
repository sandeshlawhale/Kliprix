"""Render one clip of a downloaded video and check the result (M6 dev tool).

Usage (from `backend/`):
    python -m app.cli.render_clip <folder> --start S --end E [--title T]
        [--style pop|karaoke|boxed] [--crop auto|face|full] [--blur 30] [--preview]
        [--out FILE] [--frames DIR]

`<folder>` holds `source.mp4` + `info.json` (and `transcript.json` for captions). Prints the
crop decision, timings, peak memory, stream durations (A/V sync), and extracts frames at
1, 5 and 10 s into `--frames` for a visual check.
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

from app.config import load_config
from app.core.errors import AppError
from app.core.hardware import MemoryInfo, read_memory, require_ffmpeg
from app.core.metrics import TreeMemorySampler, peak_memory_gb
from app.core.proc import CommandError, run_command
from app.pipeline.clip_render import ClipSpec, SourceVideo, render_clip
from app.pipeline.download import load_cached as load_download
from app.pipeline.select import all_words, words_between
from app.pipeline.transcribe import load_cached as load_transcript
from app.render.compose import Quality, make_thumbnail, probe_streams
from app.sources.base import SOURCE_FILE_NAME

FRAME_TIMES = (1.0, 5.0, 10.0)
SYNC_LIMIT_S = 0.1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--style", choices=["pop", "karaoke", "boxed"], default="pop")
    parser.add_argument("--crop", choices=["auto", "face", "full"], default="auto")
    parser.add_argument("--blur", type=int, default=30)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument(
        "--low-ram", action="store_true", help="act as on a 4 GB machine (ffmpeg thread limit)"
    )
    parser.add_argument("--work", type=Path, help="work folder (default <folder>/render-work)")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--frames", type=Path)
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    folder: Path = args.folder
    config = load_config()
    info = load_download(folder, config.ffmpeg_path)
    if info is None:
        print("ERROR: no valid source.mp4 + info.json in this folder", file=sys.stderr)
        return 1
    words = [
        word.model_copy(update={"start": word.start - args.start, "end": word.end - args.start})
        for word in words_between(all_words(load_transcript(folder)), args.start, args.end)
    ]
    spec = ClipSpec(
        start_s=args.start, end_s=args.end, title=args.title, show_title=bool(args.title),
        style=args.style, crop=args.crop, blur=args.blur, words=words,
    )  # fmt: skip
    source = SourceVideo(folder / SOURCE_FILE_NAME, info.probed_duration_s, info.width, info.height)
    quality: Quality = "preview" if args.preview else "final"
    work: Path = args.work or folder / "render-work"
    out: Path = args.out or folder / f"clip-{args.style}-{args.crop}-{quality}.mp4"
    memory = MemoryInfo(total_gb=4.0, available_gb=2.0) if args.low_ram else read_memory()
    started = time.monotonic()
    sampler = TreeMemorySampler()
    try:
        with sampler:
            result = render_clip(
            source, work, spec, out, quality,
            models_dir=config.data_dir / "models", ffmpeg_path=config.ffmpeg_path,
            memory=memory, on_stage=lambda name: print(f"stage: {name}", flush=True),
        )  # fmt: skip
        thumb = make_thumbnail(out, out.with_suffix(".jpg"), ffmpeg_path=config.ffmpeg_path)
        streams = probe_streams(out, config.ffmpeg_path)
    except (AppError, CommandError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    crop = result.crop
    print(f"crop: mode={crop.mode} face_ratio={crop.face_ratio} warning={crop.warning}")
    print(f"words: {len(words)}; output: {out} ({out.stat().st_size} bytes); thumbnail {thumb}")
    print(
        f"streams: {streams.width}x{streams.height} @ {streams.fps}, video {streams.video_s:.3f} s,"
        f" audio {streams.audio_s} s"
    )
    if streams.audio_s is not None:
        diff = abs(streams.video_s - streams.audio_s)
        verdict = "OK" if diff < SYNC_LIMIT_S else "TOO BIG"
        print(f"A/V duration difference: {diff * 1000:.0f} ms ({verdict})")
    print(
        f"time: {time.monotonic() - started:.1f} s; peak memory: backend "
        f"{peak_memory_gb():.2f} GB, backend + ffmpeg {sampler.peak_gb:.2f} GB"
    )
    if args.frames:
        frames: Path = args.frames
        frames.mkdir(parents=True, exist_ok=True)
        ffmpeg = require_ffmpeg(config.ffmpeg_path)
        for at in FRAME_TIMES:
            target = frames / f"{out.stem}-{at:.0f}s.png"
            run_command(
                [ffmpeg, "-loglevel", "error", "-y", "-ss", str(at), "-i", str(out),
                 "-frames:v", "1", str(target)],
                60,
            )  # fmt: skip
            print(f"frame: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
