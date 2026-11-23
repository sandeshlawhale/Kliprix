"""Synthetic timelines for the contextual clipping tests (D-050): words, on-screen text and
sound events at chosen times, turned into a `ClipContext` the way the pipeline does it."""

from __future__ import annotations

from app.pipeline.context.strategies import GameStrategy
from app.pipeline.context.timeline import ClipContext, EventIds, build_context, rows_from_evidence
from app.pipeline.context.utterances import segment
from app.pipeline.screen_text import ScreenText
from app.pipeline.sound import SoundEvents
from app.pipeline.transcribe import Word

STEP = 0.4
DURATION = 600.0


def say(start: float, text: str, step: float = STEP) -> list[Word]:
    """One word every `step` seconds from `start`, each lasting 0.3 s."""
    return [
        Word(text=token, start=round(start + i * step, 2), end=round(start + i * step + 0.3, 2))
        for i, token in enumerate(text.split())
    ]


def speech(*items: tuple[float, str]) -> list[Word]:
    words: list[Word] = []
    for start, text in items:
        words += say(start, text)
    return words


def context(
    strategy: GameStrategy,
    words: list[Word] | None = None,
    *,
    texts: dict[float, list[str]] | None = None,
    sounds: dict[float, dict[str, float]] | None = None,
    zone: tuple[float, float] = (0.0, DURATION),
    duration: float = DURATION,
) -> ClipContext:
    """`texts`: on-screen text by time; `sounds`: sound groups by time (2 s windows)."""
    windows = [dict((sounds or {}).get(float(i * 2), {})) for i in range(int(duration // 2))]
    # Empty frames every 4 s so a text shown once is not taken for a stream overlay.
    frames: dict[str, list[str]] = {f"{t:.2f}": [] for t in range(0, int(duration), 4)}
    for t, items in (texts or {}).items():
        frames[f"{t:.2f}"] = items
    utterances = segment(words or [])
    rows = rows_from_evidence(
        zone[0],
        zone[1],
        screen=None,
        events=SoundEvents(window_s=2, windows=windows),
        text=ScreenText(frames=frames),
        utterances=utterances,
    )
    return build_context(zone[0], zone[1], duration, rows, strategy, utterances, EventIds())
