"""Sentence edges and speech density from transcript words (D-033). Pure functions, no I/O.

A sentence ends after a word ending in . ? ! । or … , or before a pause of at least
`SENTENCE_PAUSE_S` (Hinglish transcripts often have little punctuation). Clip edges snap to
these so a short never starts or stops in the middle of a sentence.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass

from app.config import SENTENCE_PAUSE_S
from app.pipeline.transcribe import Word

# Full stop, question and exclamation marks, the Devanagari danda, and the ellipsis character.
SENTENCE_END_MARKS = (".", "?", "!", chr(0x0964), chr(0x2026))
# Closing quotes (straight and curly) and brackets after the mark still end the sentence.
_TRAILING = "\"')] " + chr(0x201D) + chr(0x2019)


@dataclass(frozen=True)
class SentenceEdges:
    """Sorted times where sentences start (first word's start) and end (last word's end)."""

    starts: list[float]
    ends: list[float]


def ends_sentence(word: Word) -> bool:
    return word.text.rstrip(_TRAILING).endswith(SENTENCE_END_MARKS)


def sentence_edges(words: Sequence[Word], pause_s: float = SENTENCE_PAUSE_S) -> SentenceEdges:
    ordered = sorted(words, key=lambda word: word.start)
    starts: list[float] = []
    ends: list[float] = []
    for index, word in enumerate(ordered):
        if index == 0:
            starts.append(word.start)
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        if following is None or ends_sentence(word) or following.start - word.end >= pause_s:
            ends.append(word.end)
            if following is not None:
                starts.append(following.start)
    return SentenceEdges(starts=starts, ends=ends)


def nearest(times: Sequence[float], t: float, within: float) -> float | None:
    """The time in sorted `times` closest to `t`, if it is at most `within` away."""
    index = bisect.bisect_left(times, t)
    options = [times[i] for i in (index - 1, index) if 0 <= i < len(times)]
    best = min(options, key=lambda value: abs(value - t), default=None)
    return best if best is not None and abs(best - t) <= within else None


def last_between(times: Sequence[float], low: float, high: float) -> float | None:
    """The latest time in sorted `times` within [low, high]."""
    index = bisect.bisect_right(times, high) - 1
    return times[index] if index >= 0 and times[index] >= low else None


def first_between(times: Sequence[float], low: float, high: float) -> float | None:
    """The earliest time in sorted `times` within [low, high]."""
    index = bisect.bisect_left(times, low)
    return times[index] if index < len(times) and times[index] <= high else None


def words_per_second(words: Sequence[Word], start: float, end: float) -> float:
    """Words that start inside [start, end) per second of that window."""
    if end <= start:
        return 0.0
    return sum(1 for word in words if start <= word.start < end) / (end - start)
