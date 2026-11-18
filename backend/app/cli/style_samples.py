"""Render one example frame per caption style with the real renderer (docs/03 "Edit video
style": examples made from the real renderer, not mock screenshots).

Usage (from `backend/`):
    python -m app.cli.style_samples --out ../frontend/public/style-samples

The background is ffmpeg's synthetic `testsrc2` pattern (no copyrighted media). Writes
`pop.png`, `karaoke.png`, `boxed.png` (540x960).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from app.config import load_config
from app.core.errors import AppError
from app.core.hardware import read_memory, require_ffmpeg
from app.core.proc import CommandError, run_command
from app.pipeline.clip_render import ClipSpec, SourceVideo, render_clip
from app.pipeline.transcribe import Word

SAMPLE_SECONDS = 4.0
# The frame is taken while the second word is active, so the highlight is visible.
FRAME_AT_S = 0.6
SAMPLE_WORDS = ("This", "is", "how", "captions", "look")
SRC_W, SRC_H = 1280, 720
TIMEOUT_S = 120.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    config = load_config()
    try:
        ffmpeg = require_ffmpeg(config.ffmpeg_path)
        with tempfile.TemporaryDirectory(prefix="kliprix-samples ") as temp:
            folder = Path(temp)
            source = folder / "source.mp4"
            make = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"testsrc2=s={SRC_W}x{SRC_H}:r=30:d={SAMPLE_SECONDS}",
                "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={SAMPLE_SECONDS}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source),
            ]  # fmt: skip
            if run_command(make, TIMEOUT_S).returncode != 0:
                print("ERROR: could not make the sample video", file=sys.stderr)
                return 1
            words = [
                Word(text=text, start=index * 0.4, end=index * 0.4 + 0.35)
                for index, text in enumerate(SAMPLE_WORDS)
            ]
            for style in ("pop", "karaoke", "boxed"):
                spec = ClipSpec(
                    start_s=0.0, end_s=SAMPLE_SECONDS, title="Your title here", show_title=True,
                    style=style, crop="full", blur=30, words=words,
                )  # fmt: skip
                video = folder / f"{style}.mp4"
                render_clip(
                    SourceVideo(source, SAMPLE_SECONDS, SRC_W, SRC_H), folder / f"work-{style}",
                    spec, video, "preview", models_dir=config.data_dir / "models",
                    ffmpeg_path=config.ffmpeg_path, memory=read_memory(),
                )  # fmt: skip
                target = out / f"{style}.png"
                frame = [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(FRAME_AT_S),
                    "-i", str(video), "-frames:v", "1", str(target),
                ]  # fmt: skip
                if run_command(frame, TIMEOUT_S).returncode != 0:
                    print(f"ERROR: could not extract the {style} frame", file=sys.stderr)
                    return 1
                print(f"sample: {target}")
    except (AppError, CommandError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
