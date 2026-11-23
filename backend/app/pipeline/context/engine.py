"""The contextual clipping engine (D-050): from highlight candidates to coherent clips.

    candidate -> context reconstruction -> moment template -> boundary resolution
              -> validation -> deterministic repair -> (critic for soft issues) -> ranking

Every chosen clip passed the quality gate; clips that cannot be repaired are rejected, and
fewer good clips beat a full set of broken ones.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.config import (
    CLIP_MIN_GAP_S,
    CLIP_SCORE_MIN,
    CRITIC_ENABLED,
    CRITIC_MAX_CLIPS,
    DUPLICATE_WORD_SHARE,
)
from app.llm.client import LlmClient
from app.pipeline.context.boundaries import Core, Hints, Limits, Resolution, find_core, resolve
from app.pipeline.context.critic import critique
from app.pipeline.context.quality import Defect, Issue, Repaired, repair
from app.pipeline.context.scoring import Factors, factors, word_overlap
from app.pipeline.context.strategies import GameStrategy, MomentTemplate
from app.pipeline.context.timeline import (
    Boundary,
    ClipContext,
    EventIds,
    build_context,
    rows_from_evidence,
    zone_for,
)
from app.pipeline.context.utterances import UtteranceIndex, segment
from app.pipeline.review import ClipAnalysis
from app.pipeline.screen import ScreenFile
from app.pipeline.screen_text import ScreenText
from app.pipeline.sound import SoundEvents
from app.pipeline.transcribe import Word

logger = logging.getLogger(__name__)

# Without LLM ids, a clip keeps at least this share of the LLM's draft window.
DRAFT_KEEP_SHARE = 0.6


def _kept(fixed: Repaired, moment: Moment) -> float:
    length = moment.end - moment.start
    if length <= 0:
        return 1.0
    return max(0.0, min(fixed.end, moment.end) - max(fixed.start, moment.start)) / length


# Soft defects that send a clip to the critic.
CRITIC_DEFECTS = frozenset({Defect.PAYOFF_UNVERIFIED, Defect.DANGLING_END})


@dataclass(frozen=True)
class MomentRefs:
    """What the LLM named by id (D-050)."""

    setup: str | None = None
    core: tuple[str, ...] = ()
    payoff: str | None = None
    end_after: str | None = None
    multi_round: bool = False


@dataclass(frozen=True)
class Moment:
    """One candidate clip for the engine. `key` is the caller's index; `group` orders the
    editorial choice (0 kept by the reviewer or written by the LLM, 1 filled, 2 dropped by
    the reviewer: used only when nothing else is usable)."""

    key: int
    start: float
    end: float
    llm_score: float
    limits: Limits
    moment_type: str = ""
    refs: MomentRefs = MomentRefs()
    group: int = 0
    analysis: ClipAnalysis | None = None


@dataclass
class Placed:
    moment: Moment
    start: float
    end: float
    template: MomentTemplate
    resolution: Resolution
    ctx: ClipContext
    issues: list[Issue]
    factors: Factors
    score: float
    notes: list[str] = field(default_factory=list)

    @property
    def defects(self) -> set[Defect]:
        return {issue.defect for issue in self.issues}

    def summary(self) -> list[str]:
        """Short quality notes kept with the clip in selection.json."""
        found = [f"{issue.defect.value}: {issue.detail}" for issue in self.issues]
        return (self.notes + found)[:8]


class ContextEngine:
    """One per selection: utterances and event ids are shared by all clips of the video."""

    def __init__(
        self,
        strategy: GameStrategy,
        words: Sequence[Word],
        duration_s: float,
        *,
        screen: ScreenFile | None = None,
        events: SoundEvents | None = None,
        text: ScreenText | None = None,
    ) -> None:
        self.strategy = strategy
        self.duration_s = duration_s
        self.utterances: UtteranceIndex = segment(words)
        self.ids = EventIds()
        self._screen, self._events, self._text = screen, events, text
        self._contexts: dict[tuple[float, float, int], ClipContext] = {}

    # ----- context -----

    def context(
        self, start: float, end: float, analysis: ClipAnalysis | None = None
    ) -> ClipContext:
        """The context around [start, end): from the draft's close analysis when there is
        one (cached per analysis), else from the whole-video evidence (cached per zone)."""
        if analysis is not None:
            zone = (analysis.zone_start, analysis.zone_end)
        else:
            zone = zone_for(start, end, self.duration_s)
        key = (zone[0], zone[1], id(analysis) if analysis is not None else 0)
        cached = self._contexts.get(key)
        if cached is not None:
            return cached
        rows = (
            analysis.rows
            if analysis is not None and analysis.rows
            else rows_from_evidence(
                zone[0],
                zone[1],
                screen=self._screen,
                events=self._events,
                text=self._text,
                utterances=self.utterances,
            )
        )
        ctx = build_context(
            zone[0], zone[1], self.duration_s, rows, self.strategy, self.utterances, self.ids
        )
        if analysis is not None and self.strategy.generic_scene_ends:
            known = {round(b.t) for b in ctx.boundaries}
            ctx.boundaries += [
                Boundary(t, "scene", why) for t, why in analysis.scene_ends if round(t) not in known
            ]
            ctx.boundaries.sort(key=lambda b: b.t)
        self._contexts[key] = ctx
        return ctx

    # ----- one clip -----

    def _from_refs(
        self, refs: MomentRefs, ctx: ClipContext
    ) -> tuple[Core | None, float | None, float | None, list[str]]:
        notes: list[str] = []
        unknown: list[str] = []

        def span(ref: str | None) -> tuple[float, float] | None:
            if not ref:
                return None
            utterance = ctx.utterances.get(ref)
            if (
                utterance is not None
                and utterance.end > ctx.zone_start
                and utterance.start < ctx.zone_end
            ):
                return utterance.start, utterance.end
            event = ctx.event(ref)
            if event is not None:
                return event.t, event.end
            unknown.append(ref[:12])
            return None

        spans = [s for s in (span(ref) for ref in refs.core) if s is not None]
        core = (
            Core(min(s[0] for s in spans), max(s[1] for s in spans), exact=True) if spans else None
        )
        setup = span(refs.setup)
        ends = [s[1] for s in (span(refs.payoff), span(refs.end_after)) if s is not None]
        if unknown:
            notes.append("unknown ids ignored: " + ", ".join(unknown))
        return core, setup[0] if setup else None, max(ends) if ends else None, notes

    def place(self, moment: Moment) -> Placed | None:
        """Resolve, validate and repair one moment; None when it cannot be made coherent."""
        ctx = self.context(moment.start, moment.end, moment.analysis)
        template = self.strategy.template(moment.moment_type)
        core, setup_at, end_at, notes = self._from_refs(moment.refs, ctx)
        named = core is not None
        if core is None:
            core = find_core(ctx, template, moment.start, moment.end)
            hints = Hints(
                start=moment.start,
                end=moment.end,
                setup_at=setup_at,
                end_at=end_at,
                multi_round=moment.refs.multi_round,
            )
        else:
            notes.append("core from the LLM's ids")
            hints = Hints(setup_at=setup_at, end_at=end_at, multi_round=moment.refs.multi_round)
        resolution, fixed = self._resolved(ctx, template, core, moment, hints)
        if not named and core.exact and fixed.ok and _kept(fixed, moment) < DRAFT_KEEP_SHARE:
            # Events moved the clip away from what the LLM chose: keep its window instead.
            plain = find_core(ctx, template, moment.start, moment.end, events=False)
            other, other_fixed = self._resolved(ctx, template, plain, moment, hints)
            if other_fixed.ok and _kept(other_fixed, moment) > _kept(fixed, moment):
                resolution, fixed = other, other_fixed
                notes.append("kept the LLM's window (the events pointed elsewhere)")
        if not fixed.ok:
            logger.info(
                "context rejected %s %.1f-%.1f: %s",
                moment.key,
                moment.start,
                moment.end,
                "; ".join(f"{i.defect.value} ({i.detail})" for i in fixed.hard)[:200],
            )
            return None
        notes += resolution.notes + fixed.log + ctx.notes
        score_factors = factors(
            fixed.start, fixed.end, resolution, ctx, fixed.issues, moment.llm_score
        )
        logger.info(
            "context placed %s %s/%s %.1f-%.1f -> %.1f-%.1f score %.2f issues %s",
            moment.key,
            self.strategy.name,
            template.name,
            moment.start,
            moment.end,
            fixed.start,
            fixed.end,
            score_factors.total(),
            ",".join(i.defect.value for i in fixed.issues) or "none",
        )
        return Placed(
            moment=moment,
            start=fixed.start,
            end=fixed.end,
            template=template,
            resolution=resolution,
            ctx=ctx,
            issues=fixed.issues,
            factors=score_factors,
            score=round(score_factors.total(), 3),
            notes=notes,
        )

    def _resolved(
        self,
        ctx: ClipContext,
        template: MomentTemplate,
        core: Core,
        moment: Moment,
        hints: Hints,
    ) -> tuple[Resolution, Repaired]:
        resolution = resolve(ctx, template, core, moment.limits, hints)
        fixed = repair(resolution, ctx, moment.limits)
        if not fixed.ok:
            # The draft's own edges, repaired, as a second chance.
            second = repair(resolution, ctx, moment.limits, moment.start, moment.end)
            if second.ok:
                fixed = second
        return resolution, fixed

    # ----- the set -----

    def _words(self, start: float, end: float) -> list[str]:
        return [w.text.strip().lower() for w in self.utterances.words if start <= w.start < end]

    def pick(
        self, placed: Sequence[Placed], count: int, taken: Sequence[tuple[float, float]] = ()
    ) -> list[Placed]:
        """The best `count` clips: editorial group first, then score; no overlap, no clip
        within the minimum gap of another or of `taken`, no duplicates, no clip under
        CLIP_SCORE_MIN (unless it would be the only one)."""
        chosen: list[Placed] = []
        for groups in ((0, 1), (2,)):
            if chosen:
                break
            pool = sorted(
                (p for p in placed if p.moment.group in groups),
                key=lambda p: (p.moment.group, -p.score, p.moment.key),
            )
            for item in pool:
                if len(chosen) >= count:
                    break
                windows = [(c.start, c.end) for c in chosen] + list(taken)
                if any(
                    item.start < high + CLIP_MIN_GAP_S and item.end > low - CLIP_MIN_GAP_S
                    for low, high in windows
                ):
                    continue
                words = self._words(item.start, item.end)
                overlap = max(
                    (word_overlap(words, self._words(c.start, c.end)) for c in chosen), default=0.0
                )
                if overlap >= DUPLICATE_WORD_SHARE:
                    logger.info("context duplicate dropped %s", item.moment.key)
                    continue
                if overlap > 0:
                    item.factors = factors(
                        item.start,
                        item.end,
                        item.resolution,
                        item.ctx,
                        item.issues,
                        item.moment.llm_score,
                        novelty=1 - overlap,
                    )
                    item.score = round(item.factors.total(), 3)
                if item.score < CLIP_SCORE_MIN and chosen:
                    logger.info("context low score dropped %s (%.2f)", item.moment.key, item.score)
                    continue
                chosen.append(item)
        return chosen

    def select(
        self,
        moments: Sequence[Moment],
        count: int,
        taken: Sequence[tuple[float, float]] = (),
        client: LlmClient | None = None,
        title: str = "",
    ) -> list[Placed]:
        placed = [p for p in (self.place(m) for m in moments) if p is not None]
        chosen = self.pick(placed, count, taken)
        if client is None or not CRITIC_ENABLED:
            return chosen
        flagged = [p for p in chosen if p.defects & CRITIC_DEFECTS][:CRITIC_MAX_CLIPS]
        if not flagged:
            return chosen
        verdicts = critique(
            client,
            title,
            self.strategy.guidance,
            [
                (
                    p.moment.key,
                    p.start,
                    p.end,
                    p.template.shape,
                    [f"{i.defect.value}: {i.detail}" for i in p.issues],
                    p.ctx,
                )
                for p in flagged
            ],
        )
        dropped: set[int] = set()
        for item in flagged:
            verdict = verdicts.get(item.moment.key)
            if verdict is None:
                continue
            if verdict.start is not None or verdict.end is not None:
                again = repair(
                    item.resolution,
                    item.ctx,
                    item.moment.limits,
                    verdict.start if verdict.start is not None else item.start,
                    verdict.end if verdict.end is not None else item.end,
                )
                if again.ok:
                    item.start, item.end, item.issues = again.start, again.end, again.issues
                    item.notes.append("edges moved by the critic")
                    item.factors = factors(
                        item.start,
                        item.end,
                        item.resolution,
                        item.ctx,
                        item.issues,
                        item.moment.llm_score,
                    )
                    item.score = round(item.factors.total(), 3)
                else:
                    item.notes.append("critic's edge move refused by the rules")
            item.notes += [f"critic: {issue}" for issue in verdict.issues]
            if not verdict.worth_sharing and not verdict.valid:
                dropped.add(item.moment.key)
        if dropped and len(chosen) > len(dropped):
            logger.info("critic dropped %s", sorted(dropped))
            return self.pick([p for p in placed if p.moment.key not in dropped], count, taken)
        return chosen
