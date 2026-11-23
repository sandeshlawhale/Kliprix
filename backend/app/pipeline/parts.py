"""Montage parts (D-051): a clip made of several source ranges played back to back.

Parts are `(start, end)` source-video seconds, sorted and not overlapping. A clip with fewer
than two parts is one continuous piece from its start to its end. The clip's own timeline
("output time") starts at 0 and runs over the parts in order, so caption words and blur
boxes of a montage clip are in output time.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence

from app.pipeline.transcribe import Word

Part = tuple[float, float]
# Parts closer than this are one part; shorter ones are dropped.
JOIN_TOLERANCE_S = 0.05
TIME_DECIMALS = 2


def normalized(parts: Sequence[Sequence[float]]) -> tuple[Part, ...]:
    """Sorted, merged where they touch or overlap, without empty parts."""
    ordered = sorted((float(p[0]), float(p[1])) for p in parts if len(p) == 2)
    merged: list[Part] = []
    for start, end in ordered:
        if end - start < JOIN_TOLERANCE_S:
            continue
        if merged and start <= merged[-1][1] + JOIN_TOLERANCE_S:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def total(parts: Sequence[Part]) -> float:
    return sum(end - start for start, end in parts)


def effective(start: float, end: float, parts: Sequence[Sequence[float]]) -> tuple[Part, ...]:
    """The ranges a clip plays: its parts kept inside [start, end] (a trim moves the first
    part's start and the last part's end to the new edges), or the single range
    [start, end] when fewer than two parts remain."""
    inside = [(max(start, s), min(end, e)) for s, e in normalized(parts) if e > start and s < end]
    kept = list(normalized(inside))
    if len(kept) < 2:
        return ((start, end),)
    kept[0] = (start, kept[0][1])
    kept[-1] = (kept[-1][0], end)
    return tuple(kept)


def is_montage(parts: Sequence[Part]) -> bool:
    return len(parts) > 1


def to_output(t: float, parts: Sequence[Part]) -> float | None:
    """Output time of source time `t`, or None when `t` is in a skipped gap or outside."""
    offset = 0.0
    for start, end in parts:
        if start - 1e-6 <= t <= end + 1e-6:
            return offset + min(max(t, start), end) - start
        offset += end - start
    return None


def to_source(t: float, parts: Sequence[Part]) -> float:
    """Source time of output time `t` (clamped to the clip)."""
    offset = 0.0
    for start, end in parts:
        length = end - start
        if t <= offset + length + 1e-6:
            return start + max(0.0, t - offset)
        offset += length
    return parts[-1][1] if parts else t


def words_for(words: Sequence[Word], parts: Sequence[Part]) -> list[Word]:
    """Caption words for a clip: transcript words (source seconds) whose middle lies in a
    part, in output time and clamped to that part."""
    found: list[Word] = []
    offset = 0.0
    for start, end in parts:
        for word in words:
            middle = (word.start + word.end) / 2
            if start <= middle < end:
                found.append(
                    Word(
                        text=word.text,
                        start=round(offset + max(word.start, start) - start, TIME_DECIMALS),
                        end=round(offset + min(word.end, end) - start, TIME_DECIMALS),
                    )
                )
        offset += end - start
    return found


def skipped(parts: Sequence[Part]) -> float:
    """Seconds left out between the parts."""
    return sum(b[0] - a[1] for a, b in itertools.pairwise(parts))
