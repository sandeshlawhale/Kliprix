"""Clip ranking (D-050): weighted factors, each 0-1, weights in `CLIP_SCORE_WEIGHTS`.

Only clips that passed the quality gate are scored: a high entertainment score never excuses
a broken clip. Coherence and context completeness carry a large share of the weight.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.config import CLIP_SCORE_WEIGHTS, DANGLING_STARTS, EDGE_TOLERANCE_S
from app.pipeline.context.boundaries import Resolution
from app.pipeline.context.quality import REPAIRABLE, Defect, Issue
from app.pipeline.context.timeline import ClipContext, kind_matches


@dataclass(frozen=True)
class Factors:
    entertainment: float
    coherence: float
    context: float
    payoff: float
    audio: float
    visual: float
    novelty: float
    standalone: float

    def total(self, weights: Mapping[str, float] = CLIP_SCORE_WEIGHTS) -> float:
        values: dict[str, float] = dict(self.__dict__)
        used = {name: w for name, w in weights.items() if name in values and w > 0}
        if not used:
            return 0.0
        return sum(values[name] * w for name, w in used.items()) / sum(used.values())


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def factors(
    start: float,
    end: float,
    resolution: Resolution,
    ctx: ClipContext,
    issues: list[Issue],
    entertainment: float,
    novelty: float = 1.0,
) -> Factors:
    defects = {issue.defect for issue in issues}
    coherence = 1.0 - 0.2 * len(defects & REPAIRABLE)
    if Defect.DANGLING_END in defects:
        coherence -= 0.3
    core, template = resolution.core, resolution.template
    context = 1.0
    if core.exact and template.min_setup_s > 0:
        context = _clamp((core.start - start) / template.min_setup_s)
    if Defect.MISSING_SETUP in defects:
        context = min(context, 0.3)
    if not template.payoff_kinds:
        payoff = 0.7
    elif any(
        kind_matches(e.kind, template.payoff_kinds) and start <= e.t < end for e in ctx.events
    ):
        payoff = 1.0
    elif Defect.MISSING_PAYOFF in defects:
        payoff = 0.0
    elif Defect.PAYOFF_UNVERIFIED in defects:
        payoff = 0.3
    else:
        payoff = 0.6
    rows = [r for r in ctx.rows if start <= r.t < end]
    audio = (
        sum(1 for r in rows if r.speech or r.sounds) / len(rows) if rows and ctx.observed else 0.5
    )
    seen = [r for r in rows if r.screen]
    visual = 1.0 - sum(r.dull for r in seen) / len(seen) if seen else 0.5
    if Defect.MOSTLY_SILENT in defects:
        audio = min(audio, 0.2)
        # Nothing heard or read at all: the LLM's judgment alone is weak evidence.
        entertainment *= 0.6
        coherence -= 0.2
    standalone = 1.0
    first = next(
        (u for u in ctx.utterances.between(start, end) if u.start >= start - EDGE_TOLERANCE_S),
        None,
    )
    if first is not None and ctx.utterances.first_word(first) in DANGLING_STARTS:
        standalone = 0.3
    elif first is not None and first.continued:
        standalone = 0.6
    return Factors(
        entertainment=_clamp(entertainment),
        coherence=_clamp(coherence),
        context=_clamp(context),
        payoff=payoff,
        audio=_clamp(audio),
        visual=_clamp(visual),
        novelty=_clamp(novelty),
        standalone=standalone,
    )


def word_overlap(a: list[str], b: list[str]) -> float:
    """Share of the shorter clip's words that also appear in the other (duplicates)."""
    if not a or not b:
        return 0.0
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    pool = set(big)
    return sum(1 for word in small if word in pool) / len(small)
