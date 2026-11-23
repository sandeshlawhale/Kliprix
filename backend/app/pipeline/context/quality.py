"""The clip quality gate and deterministic repair (D-050).

`validate` lists a clip's defects. Hard defects block rendering; repairable ones are fixed by
`repair` (at most REPAIR_MAX_ATTEMPTS rounds, each followed by a new validation); soft ones
only lower the clip's score or send it to the critic. A clip that still has a hard defect
after repair is rejected, never rendered broken.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from app.config import (
    EDGE_TOLERANCE_S,
    REPAIR_MAX_ATTEMPTS,
    SILENCE_SHARE_MAX,
    STRUCTURE_SLACK_S,
    UTTERANCE_CONNECTORS,
)
from app.pipeline.context.boundaries import (
    Limits,
    Resolution,
    fit_length,
    snap_end,
    snap_start,
    trim_dead_air,
)
from app.pipeline.context.montage import plan
from app.pipeline.context.timeline import ClipContext, kind_matches
from app.pipeline.parts import total


class Defect(StrEnum):
    INVALID_TIMES = "invalid_times"
    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"
    START_MID_UTTERANCE = "start_mid_utterance"
    END_MID_UTTERANCE = "end_mid_utterance"
    CROSSES_BOUNDARY = "crosses_boundary"
    MISSING_CORE = "missing_core"
    MISSING_PAYOFF = "missing_payoff"
    MISSING_SETUP = "missing_setup"
    DEAD_AIR_START = "dead_air_start"
    DEAD_AIR_END = "dead_air_end"
    PAYOFF_UNVERIFIED = "payoff_unverified"
    # Talk runs across a round or scene edge, so the clip cuts it there (at a word gap).
    CUT_AT_STRUCTURE = "cut_at_structure"
    DANGLING_END = "dangling_end"
    MOSTLY_SILENT = "mostly_silent"


# Cannot be rendered as they are; repaired or rejected.
HARD = frozenset(
    {
        Defect.INVALID_TIMES,
        Defect.TOO_SHORT,
        Defect.TOO_LONG,
        Defect.START_MID_UTTERANCE,
        Defect.END_MID_UTTERANCE,
        Defect.CROSSES_BOUNDARY,
        Defect.MISSING_CORE,
    }
)
# Fixed when possible; the clip stays if not (with a lower score).
REPAIRABLE = frozenset(
    {
        Defect.MISSING_PAYOFF,
        Defect.MISSING_SETUP,
        Defect.DEAD_AIR_START,
        Defect.DEAD_AIR_END,
    }
)
# Judged by the score and, when enabled, the critic.
SOFT = frozenset({
    Defect.PAYOFF_UNVERIFIED, Defect.DANGLING_END, Defect.MOSTLY_SILENT,
    Defect.CUT_AT_STRUCTURE,
})  # fmt: skip


@dataclass(frozen=True)
class Issue:
    defect: Defect
    detail: str


def validate(
    start: float, end: float, resolution: Resolution, ctx: ClipContext, limits: Limits
) -> list[Issue]:
    issues: list[Issue] = []
    if (
        not (math.isfinite(start) and math.isfinite(end))
        or start < 0
        or end > ctx.duration_s + 1e-6
        or end <= start
    ):
        return [Issue(Defect.INVALID_TIMES, f"{start}-{end} is not a range inside the video")]
    # A montage (D-051) plays only its parts: that is the length that counts.
    parts = plan(start, end, resolution.template, ctx, limits.min_s) or ((start, end),)
    length = total(parts)
    if length < limits.min_s - 1e-6:
        issues.append(Issue(Defect.TOO_SHORT, f"{length:.1f} s < {limits.min_s:.0f} s"))
    if length > limits.max_s + 1e-6:
        issues.append(Issue(Defect.TOO_LONG, f"{length:.1f} s > {limits.max_s:.0f} s"))
    utterances = ctx.utterances
    tol = EDGE_TOLERANCE_S
    at_start = utterances.at(start, tol)
    if utterances.word_at(start, 0.05) is not None:
        issues.append(Issue(Defect.START_MID_UTTERANCE, "starts inside a word"))
    elif at_start is not None:
        if _at_structure(start, at_start.start, at_start.end, resolution, ctx):
            issues.append(Issue(Defect.CUT_AT_STRUCTURE, f"starts inside {at_start.id}"))
        else:
            issues.append(Issue(Defect.START_MID_UTTERANCE, f"starts inside {at_start.id}"))
    at_end = utterances.at(end, tol)
    if utterances.word_at(end, 0.05) is not None:
        issues.append(Issue(Defect.END_MID_UTTERANCE, "ends inside a word"))
    elif at_end is not None:
        if _at_structure(end, at_end.start, at_end.end, resolution, ctx):
            issues.append(Issue(Defect.CUT_AT_STRUCTURE, f"ends inside {at_end.id}"))
        else:
            issues.append(Issue(Defect.END_MID_UTTERANCE, f"ends inside {at_end.id}"))
    if not resolution.cross_allowed:
        for boundary in ctx.boundaries:
            if start + tol < boundary.t < end - tol:
                issues.append(
                    Issue(Defect.CROSSES_BOUNDARY, f"{boundary.reason} at {boundary.t:.0f} s")
                )
    core = resolution.core
    overlap = max(0.0, min(end, core.end) - max(start, core.start))
    # A named core must be inside; a draft window (not exact) mostly.
    share = 0.8 if core.exact else 0.5
    if core.end > core.start and overlap < share * (core.end - core.start) - 1e-6:
        issues.append(Issue(Defect.MISSING_CORE, "the core moment is not inside the clip"))
    template = resolution.template
    if template.payoff_kinds:
        reach_end = core.end + template.payoff_reach_s
        payoffs = [
            e
            for e in ctx.events
            if kind_matches(e.kind, template.payoff_kinds)
            and core.end - 1 <= e.t <= reach_end
            and e.t < resolution.high
        ]
        missed = [e for e in payoffs if e.t > end - 0.5]
        if missed and not [e for e in payoffs if e.t <= end - 0.5 and e.t >= core.start]:
            issues.append(Issue(Defect.MISSING_PAYOFF, f"{missed[0].kind} ({missed[0].id})"))
        elif not payoffs and template.needs_payoff and ctx.observed:
            issues.append(Issue(Defect.PAYOFF_UNVERIFIED, f"no {template.name} payoff found"))
    if core.exact and template.min_setup_s > 0:
        setup = core.start - start
        if setup < template.min_setup_s * 0.5 and start > resolution.low + 1:
            issues.append(Issue(Defect.MISSING_SETUP, f"{max(0.0, setup):.0f} s before the core"))
    if ctx.observed:
        # Dead air only counts when cutting it keeps the minimum length.
        cut_start, cut_end, _ = trim_dead_air(start, end, core, template, ctx)
        if cut_start > start + 0.5 and end - cut_start >= limits.min_s:
            issues.append(Issue(Defect.DEAD_AIR_START, f"{cut_start - start:.0f} s of nothing"))
        if cut_end < end - 0.5 and cut_end - start >= limits.min_s:
            issues.append(Issue(Defect.DEAD_AIR_END, f"{end - cut_end:.0f} s of nothing"))
        rows = [r for r in ctx.rows if any(a <= r.t < b for a, b in parts)]
        if rows:
            quiet = [r for r in rows if not (r.sounds or r.speech or r.text)]
            if len(quiet) / len(rows) > SILENCE_SHARE_MAX:
                issues.append(Issue(Defect.MOSTLY_SILENT, f"{len(quiet)}/{len(rows)} rows silent"))
    inside = [u for u in utterances.between(start, end) if u.end <= end + tol]
    if inside:
        last = inside[-1]
        following = utterances.following(last)
        answer_next = following is not None and following.start - last.end <= 2.0
        if last.text.rstrip("\"') ").endswith("?") and answer_next:
            issues.append(Issue(Defect.DANGLING_END, f"ends on the question {last.id}"))
        elif utterances.last_word(last) in UTTERANCE_CONNECTORS and answer_next:
            issues.append(Issue(Defect.DANGLING_END, f"ends mid-thought on {last.id}"))
    return issues


def _at_structure(
    edge: float, first: float, last: float, resolution: Resolution, ctx: ClipContext
) -> bool:
    """True when an utterance [first, last] is cut at `edge` because a structural edge (or
    the segment's own edge) lies inside the utterance near the cut: unavoidable, soft."""
    edges = [b.t for b in ctx.boundaries]
    if not resolution.cross_allowed:
        edges += [resolution.low, resolution.high]
    return any(first < t < last and abs(t - edge) <= STRUCTURE_SLACK_S for t in edges)


@dataclass
class Repaired:
    start: float
    end: float
    issues: list[Issue]
    log: list[str] = field(default_factory=list)

    @property
    def hard(self) -> list[Issue]:
        return [i for i in self.issues if i.defect in HARD]

    @property
    def ok(self) -> bool:
        return not self.hard


def _fix(
    defect: Defect,
    start: float,
    end: float,
    resolution: Resolution,
    ctx: ClipContext,
    limits: Limits,
) -> tuple[float, float]:
    core, template = resolution.core, resolution.template
    low, high = resolution.low, resolution.high
    if defect == Defect.START_MID_UTTERANCE:
        return snap_start(start, low, core.start, ctx), end
    if defect == Defect.END_MID_UTTERANCE:
        return start, snap_end(end, core.end, high, ctx)
    if defect == Defect.CROSSES_BOUNDARY:
        for boundary in ctx.boundaries:
            if start + EDGE_TOLERANCE_S < boundary.t < end - EDGE_TOLERANCE_S:
                if boundary.t <= core.start + EDGE_TOLERANCE_S:
                    start = boundary.t
                else:
                    end = boundary.t
        return start, end
    if defect == Defect.MISSING_CORE:
        return min(start, core.start), max(end, core.end)
    if defect == Defect.MISSING_PAYOFF:
        payoffs = [
            e
            for e in ctx.events
            if kind_matches(e.kind, template.payoff_kinds)
            and core.end - 1 <= e.t <= core.end + template.payoff_reach_s
            and e.t < high
        ]
        if payoffs:
            end = min(high, max(end, payoffs[0].end + template.reaction_s))
            return start, snap_end(end, core.end, high, ctx)
        return start, end
    if defect == Defect.MISSING_SETUP:
        start = max(low, core.start - template.min_setup_s)
        return snap_start(start, low, core.start, ctx), end
    if defect in (Defect.DEAD_AIR_START, Defect.DEAD_AIR_END):
        cut_start, cut_end, _ = trim_dead_air(start, end, core, template, ctx)
        if defect == Defect.DEAD_AIR_START:
            return snap_start(cut_start, low, core.start, ctx), end
        return start, snap_end(cut_end, core.end, high, ctx)
    if defect in (Defect.TOO_LONG, Defect.TOO_SHORT):
        start, end, _ = fit_length(start, end, core, template, limits, low, high, ctx)
        return start, end
    return start, end


def repair(
    resolution: Resolution,
    ctx: ClipContext,
    limits: Limits,
    start: float | None = None,
    end: float | None = None,
) -> Repaired:
    """Validate, fix what rules can fix, validate again; at most REPAIR_MAX_ATTEMPTS rounds."""
    start = resolution.start if start is None else start
    end = resolution.end if end is None else end
    log: list[str] = []
    issues = validate(start, end, resolution, ctx, limits)
    for attempt in range(REPAIR_MAX_ATTEMPTS):
        todo = [i for i in issues if i.defect in HARD | REPAIRABLE]
        if not todo or any(i.defect == Defect.INVALID_TIMES for i in todo):
            break
        before = (start, end)
        # Structure first, then content, then edges, then length.
        order = [
            Defect.CROSSES_BOUNDARY,
            Defect.MISSING_CORE,
            Defect.MISSING_PAYOFF,
            Defect.MISSING_SETUP,
            Defect.DEAD_AIR_START,
            Defect.DEAD_AIR_END,
            Defect.START_MID_UTTERANCE,
            Defect.END_MID_UTTERANCE,
            Defect.TOO_LONG,
            Defect.TOO_SHORT,
        ]
        for defect in order:
            if any(i.defect == defect for i in todo):
                start, end = _fix(defect, start, end, resolution, ctx, limits)
                log.append(f"repair {attempt + 1}: {defect.value}")
        start, end = round(start, 2), round(end, 2)
        issues = validate(start, end, resolution, ctx, limits)
        if (start, end) == before:
            break
    return Repaired(start=start, end=end, issues=issues, log=log)
