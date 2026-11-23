"""Montage planning (D-051): which quiet gaps inside an action moment to skip.

A gap is quiet when nobody speaks, no event happens (kill, spike, explosion, laughter...)
and no sound was heard in the close analysis. Only templates marked `montage` (kills,
clutches, rounds, GTA chaos and missions, Minecraft deaths and bosses) are planned, only
when the clip's surroundings were observed, and never across a structural edge (the clip's
span already stays inside one segment). Cuts fall in silence, so a part never starts or ends
inside a word.
"""

from __future__ import annotations

import itertools

from app.config import (
    MONTAGE_GAP_MIN_S,
    MONTAGE_LEAD_S,
    MONTAGE_MAX_PARTS,
    MONTAGE_PART_MIN_S,
    MONTAGE_SKIP_MIN_S,
    MONTAGE_SPAN_MAX_S,
    MONTAGE_TAIL_S,
    REVIEW_STEP_S,
)
from app.pipeline.context.strategies import MomentTemplate
from app.pipeline.context.timeline import ClipContext
from app.pipeline.parts import Part, normalized, total

# An event without a length still keeps this much around it.
EVENT_MIN_S = 0.5


def _busy(start: float, end: float, ctx: ClipContext) -> list[Part]:
    """Merged spans inside [start, end] where something is said, happens or is heard."""
    spans: list[Part] = [
        (w.start, w.end) for w in ctx.utterances.words if w.end > start and w.start < end
    ]
    spans += [
        (e.t, max(e.end, e.t + EVENT_MIN_S)) for e in ctx.events if e.end > start and e.t < end
    ]
    spans += [(r.t, r.t + REVIEW_STEP_S) for r in ctx.rows if r.sounds and start <= r.t < end]
    clamped = [(max(start, a), min(end, b)) for a, b in spans if min(end, b) > max(start, a)]
    return list(normalized(clamped))


def quiet_gaps(start: float, end: float, ctx: ClipContext) -> list[Part]:
    """The ranges that can be left out: between two busy spans, at least MONTAGE_GAP_MIN_S
    long, minus MONTAGE_TAIL_S after the first and MONTAGE_LEAD_S before the second, and
    removing at least MONTAGE_SKIP_MIN_S. Edges of the clip are not gaps (the resolver
    already trims dead air there)."""
    busy = _busy(start, end, ctx)
    found: list[Part] = []
    for (_, before), (after, _) in itertools.pairwise(busy):
        if after - before < MONTAGE_GAP_MIN_S:
            continue
        skip = (before + MONTAGE_TAIL_S, after - MONTAGE_LEAD_S)
        if skip[1] - skip[0] >= MONTAGE_SKIP_MIN_S:
            found.append((round(skip[0], 2), round(skip[1], 2)))
    return found


def _split(start: float, end: float, skips: list[Part]) -> list[Part]:
    parts: list[Part] = []
    cursor = start
    for a, b in sorted(skips):
        parts.append((cursor, a))
        cursor = b
    parts.append((cursor, end))
    return parts


def plan(
    start: float,
    end: float,
    template: MomentTemplate,
    ctx: ClipContext,
    min_s: float = 0.0,
) -> tuple[Part, ...]:
    """The parts of [start, end] (two or more), or () when the clip stays one piece: not
    an action template, not observed, nothing to skip, or skipping would leave it shorter
    than `min_s`. The longest gaps win when there are more than MONTAGE_MAX_PARTS parts."""
    if not template.montage or not ctx.observed or end - start > MONTAGE_SPAN_MAX_S:
        return ()
    skips = sorted(quiet_gaps(start, end, ctx), key=lambda s: s[0] - s[1])
    skips = skips[: MONTAGE_MAX_PARTS - 1]
    while skips:
        parts = _split(start, end, skips)
        short = [i for i, (a, b) in enumerate(parts) if b - a < MONTAGE_PART_MIN_S]
        if not short:
            break
        # Keep a too-short part by not skipping the smaller gap next to it.
        index = short[0]
        ordered = sorted(skips)
        neighbours = [ordered[j] for j in (index - 1, index) if 0 <= j < len(ordered)]
        smallest = min(neighbours, key=lambda s: s[1] - s[0])
        skips.remove(smallest)
    if not skips:
        return ()
    planned = tuple(_split(start, end, skips))
    if total(planned) < min_s:
        return ()
    return planned


def kept_length(
    start: float, end: float, template: MomentTemplate, ctx: ClipContext, min_s: float = 0.0
) -> float:
    """How long the clip plays: its parts' total for a montage, else its span."""
    parts = plan(start, end, template, ctx, min_s)
    return total(parts) if parts else end - start
