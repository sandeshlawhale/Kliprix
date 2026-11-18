"""Run the source stages (inspect, download, chat) for one link and print what happened.

Usage (from `backend/`):
    python -m app.cli.fetch_source <url> [--out DIR] [--inspect-only]

Default output folder: `DATA_DIR/work/cli-<source id>/`. Exit code 0 on success,
1 with `ERROR <CODE>: <message>` on a documented error (AA-020).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.config import ensure_data_dirs, load_config
from app.core.errors import AppError
from app.core.hardware import read_memory
from app.core.logging import setup_logging
from app.pipeline.chat import CHAT_FILE_NAME, run_chat
from app.pipeline.download import INFO_FILE_NAME, max_download_height, run_download
from app.sources.registry import build_adapters, resolve

PROGRESS_STEP = 0.1
SAMPLE_MESSAGES = 5


def _no_secrets() -> tuple[str, ...]:
    return ()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--out", type=Path, help="output folder")
    parser.add_argument("--inspect-only", action="store_true")
    args = parser.parse_args(argv)

    config = load_config()
    ensure_data_dirs(config)
    setup_logging(config, _no_secrets)
    try:
        adapter, url = resolve(args.url, build_adapters(config))
        print(f"url: {url}")
        meta = adapter.inspect(url)
        print(f"inspect: {meta.model_dump_json()}")
        if args.inspect_only:
            return 0
        out: Path = args.out or config.data_dir / "work" / f"cli-{meta.source_id}"
        height = max_download_height(read_memory())
        last_step = [-1]

        def show(fraction: float) -> None:
            step = int(fraction / PROGRESS_STEP)
            if step != last_step[0]:
                last_step[0] = step
                print(f"download: {fraction:.0%}", flush=True)

        info = run_download(
            adapter, url, out, ffmpeg_path=config.ffmpeg_path, max_height=height, on_progress=show
        )
        source = out / "source.mp4"
        print(
            f"download: {source} ({source.stat().st_size} bytes, {info.width}x{info.height}, "
            f"{info.probed_duration_s:.1f} s); {INFO_FILE_NAME} written"
        )
        chat = run_chat(adapter, url, out)
        if chat is None:
            print("chat: no chat replay for this video (has_chat=false); nothing written")
        else:
            print(f"chat: {len(chat.items)} messages -> {out / CHAT_FILE_NAME}")
            for message in chat.items[:SAMPLE_MESSAGES]:
                print(f"  t={message.t:.1f}s {message.text}")
    except AppError as exc:
        print(f"ERROR {exc.code.value}: {exc.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
