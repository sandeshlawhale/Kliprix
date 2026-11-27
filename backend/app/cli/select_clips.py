"""Run the select stage (one LLM call) on an analysed and transcribed video folder.

Usage (from `backend/`):
    python -m app.cli.select_clips <folder> --provider gemini --model <model id>
        [--base-url URL] [--count 5] [--extended]

The API key is asked for with a hidden prompt and kept in memory only (hard rule 4).
Prints each clip with its times and metadata. Delete `selection.json` to run it again.
"""

from __future__ import annotations

import argparse
import getpass
import io
import sys
from pathlib import Path

from app.config import load_config
from app.core.errors import AppError
from app.core.logging import setup_logging
from app.llm.client import LlmClient
from app.llm.providers import PROVIDERS_BY_ID, ProviderId
from app.llm.session import LlmSession
from app.pipeline.candidates import load_cached as load_candidates
from app.pipeline.download import load_cached as load_download
from app.pipeline.select import SELECTION_FILE_NAME, VideoFacts, run_select
from app.pipeline.transcribe import load_cached as load_transcript

MAX_CLIPS = 5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--provider", required=True, choices=sorted(PROVIDERS_BY_ID))
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url")
    parser.add_argument("--count", type=int, default=MAX_CLIPS, choices=range(1, MAX_CLIPS + 1))
    parser.add_argument("--extended", action="store_true")
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
    transcript = load_transcript(folder)
    if transcript is None:
        print("note: no transcript.json; the LLM sees signal scores only")
    api_key = getpass.getpass("API key (hidden): ").strip()
    provider: ProviderId = args.provider
    session = LlmSession(provider, args.model, api_key, args.base_url)
    setup_logging(config, lambda: (api_key,), console_level=None)
    facts = VideoFacts(info.video.title, info.video.channel, info.probed_duration_s)
    try:
        selection = run_select(
            folder, facts, candidates.items, transcript,
            count=args.count, extended=args.extended, client=LlmClient(session),
        )  # fmt: skip
    except AppError as exc:
        print(f"ERROR {exc.code.value}: {exc.message}", file=sys.stderr)
        return 1
    print(f"selection: {len(selection.clips)} clips -> {folder / SELECTION_FILE_NAME}")
    for number, clip in enumerate(selection.clips, start=1):
        print(
            f"{number}. {clip.start:.1f}-{clip.end:.1f} s ({clip.end - clip.start:.1f} s) "
            f"score {clip.score:.2f}\n   title: {clip.title}\n   youtube: {clip.youtube_title}\n"
            f"   hashtags: {' '.join(clip.hashtags)}\n   reason: {clip.reason}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
