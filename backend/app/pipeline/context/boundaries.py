"""The contextual boundary resolver (D-050).

A highlight is the core moment; the clip is the smallest coherent window that makes it
understandable. The resolver is deterministic: it chooses start and end among real timeline
boundaries (utterance edges, events, structural edges) using the moment's template:

1. The structural segment around the core (between round starts, round ends, death screens,
   scene ends) bounds the search, unless the LLM said the story spans two rounds and the
   strategy allows it. A core that itself crosses an edge keeps its stronger side.
2. Start: `min_setup_s` before the core, earlier for setup events ("1v3", the spike plant,
   the mob before a death, the exploring before a discovery) and the LLM's setup reference,
   never more than `max_setup_s`; then onto an utterance start (or past a long utterance),
   back to the setup when the first sentence continues something, and past dead air.
3. End: the core's end, then the payoff burst that follows within `payoff_reach_s` (the
   last kill, the round result, the death screen), the LLM's payoff/reaction reference, plus
   `reaction_s`; then onto an utterance end, on to the answer when it ends on a question or
   a connector, and back from trailing dead air.
4. Length: too long loses setup, then reaction, then is cut at an utterance end; too short
   grows by whole utterances (fixed padding only when there is no speech).

The LLM never sets final timestamps: it only names the core, setup and payoff (by id), and
its own times are hints inside these rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import (
    CLIP_LEAD_PAD_S,
    CLIP_TAIL_PAD_S,
    CONTEXT_FIX_REACH_S,
    CONTEXT_FIX_SENTENCES,
    DANGLING_STARTS,
    EDGE_TOLERANCE_S,
    RESOLVE_BURST_GAP_S,
    RESOLVE_DEAD_AIR_S,
    RESOLVE_END_EXPAND_S,
    RESOLVE_SETUP_LEAD_S,
    RESOLVE_START_EXPAND_S,
    UTTERANCE_CONNECTORS,
)
from app.pipeline.context.montage import kept_length
from app.pipeline.context.strategies import MomentTemplate
from app.pipeline.context.timeline import ClipContext, Event, kind_matches
from app.pipeline.context.utterances import Utterance

TIME_DECIMALS = 2
# The line before a talk clip is its setup when it ends at most this long before it.
LEAD_IN_GAP_S = 1.5
# Core events in a draft's last seconds do not define its core.
CORE_EDGE_S = 3.0


@dataclass(frozen=True)
class Core:
    """The core moment. `exact` when it was named (LLM ids) or found (core events); a core
    that is only the LLM's draft window gets no extra setup or reaction, only repairs."""

    start: float
    end: float
    exact: bool = False


@dataclass(frozen=True)
class Hints:
    """What the LLM suggested; used only inside the template's limits."""

    start: float | None = None
    end: float | None = None
    setup_at: float | None = None
    end_at: float | None = None
    multi_round: bool = False


@dataclass(frozen=True)
class Limits:
    min_s: float
    max_s: float


@dataclass
class Resolution:
    start: float
    end: float
    core: Core
    template: MomentTemplate
    # The structural segment the clip must stay in.
    low: float
    high: float
    cross_allowed: bool
    notes: list[str] = field(default_factory=list)

    @property
    def length(self) -> float:
        return self.end - self.start


def find_core(
    ctx: ClipContext, template: MomentTemplate, start: float, end: float, events: bool = True
) -> Core:
    """The core of a draft [start, end) without LLM ids: the span of its core events (the
    kills, the explosion, the death) that are not in its last CORE_EDGE_S (a banner read on
    the next scene's first frame is not the draft's moment); else the part of the draft
    where something is said or happens (not exact: no extra setup or reaction is added);
    else the draft itself. `events=False` skips the core events."""
    found = [
        e
        for e in ctx.events
        if events
        and kind_matches(e.kind, template.core_kinds)
        and e.end > start
        and e.t < max(start + 1, end - CORE_EDGE_S)
    ]
    if found:
        return Core(found[0].t, min(end, max(e.end for e in found)), exact=True)
    if ctx.observed:
        first, last = _first_busy(ctx, start, end), _last_busy(ctx, start, end)
        if first is not None and last is not None and last > first:
            return Core(max(start, first), min(end, last), exact=False)
    return Core(start, end, exact=False)


def segment_bounds(
    ctx: ClipContext, core: Core, allow_cross: bool
) -> tuple[float, float, Core, list[str]]:
    """(low, high) the clip must stay in, the core (trimmed to one side when it crossed an
    edge), and notes."""
    notes: list[str] = []
    low, high = max(0.0, ctx.zone_start), min(ctx.duration_s, ctx.zone_end)
    if allow_cross:
        return low, high, core, ["crosses a round on purpose (multi-round story)"]
    tol = EDGE_TOLERANCE_S
    inside = [b for b in ctx.boundaries if core.start + tol < b.t < core.end - tol]
    for boundary in inside:
        if not core.start + tol < boundary.t < core.end - tol:
            continue  # already left out by an earlier cut
        before = len(ctx.events_of(_ANY, core.start, boundary.t))
        after = len(ctx.events_of(_ANY, boundary.t, core.end))
        if after > before or (after == before and core.end - boundary.t > boundary.t - core.start):
            core = Core(boundary.t, core.end, core.exact)
        else:
            core = Core(core.start, boundary.t, core.exact)
        notes.append(f"core kept on one side of {boundary.reason} ({boundary.t:.0f} s)")
    for boundary in ctx.boundaries:
        if boundary.t <= core.start + tol:
            low = max(low, boundary.t)
        elif boundary.t >= core.end - tol:
            high = min(high, boundary.t)
    return low, high, core, notes


class _AnyKind(frozenset[str]):
    def __contains__(self, item: object) -> bool:
        return True


_ANY: frozenset[str] = _AnyKind()


def snap_start(t: float, low: float, core_start: float, ctx: ClipContext) -> float:
    """A start inside an utterance moves to its start when that is close (or the utterance
    belongs to the core), else past it; a start inside a word moves off the word."""
    utterances = ctx.utterances
    utterance = utterances.at(t, EDGE_TOLERANCE_S)
    if utterance is None:
        word = utterances.word_at(t, 0.05)
        if word is None:
            return t
        return word.start if word.start >= low else word.end
    if t - utterance.start <= RESOLVE_START_EXPAND_S and utterance.start >= low:
        return utterance.start
    if utterance.end <= core_start:
        return utterance.end
    if utterance.start >= low:
        return utterance.start
    # The utterance began before a structural edge (talk across a round change): start at
    # the next word, never inside one.
    return next((w.start for w in utterances.words if w.start >= t), t)


def snap_end(t: float, core_end: float, high: float, ctx: ClipContext) -> float:
    """An end inside an utterance moves on to its end when that is close (or it holds the
    core), else back before it; an end inside a word moves off the word."""
    utterances = ctx.utterances
    utterance = utterances.at(t, EDGE_TOLERANCE_S)
    if utterance is None:
        word = utterances.word_at(t, 0.05)
        if word is None:
            return t
        return word.end if word.end <= high else word.start
    if utterance.end - t <= RESOLVE_END_EXPAND_S and utterance.end <= high:
        return utterance.end
    if utterance.start >= core_end:
        return utterance.start
    if utterance.end <= high:
        return utterance.end
    # The utterance runs past a structural edge: end after the last whole word before it.
    return max((w.end for w in utterances.words if w.end <= t), default=t)


def _first_utterance(ctx: ClipContext, start: float, end: float) -> Utterance | None:
    for utterance in ctx.utterances.between(start, end):
        if utterance.start >= start - EDGE_TOLERANCE_S:
            return utterance
    return None


def back_to_setup(
    start: float, end: float, low: float, max_s: float, ctx: ClipContext
) -> tuple[float, list[str]]:
    """While the clip's first utterance continues something ("so", "and", "exactly", "toh",
    ...), start at the utterance before it (D-044, now on utterances)."""
    notes: list[str] = []
    utterances = ctx.utterances
    for _ in range(CONTEXT_FIX_SENTENCES):
        first = _first_utterance(ctx, start, end)
        if first is None or utterances.first_word(first) not in DANGLING_STARTS:
            break
        earlier = utterances.previous(first)
        if (
            earlier is None
            or earlier.start < low
            or earlier.start < start - CONTEXT_FIX_REACH_S
            or end - earlier.start > max_s
        ):
            break
        notes.append(f"start moved to the setup ({earlier.id})")
        start = earlier.start
    return start, notes


def with_lead_in(
    start: float,
    end: float,
    core: Core,
    template: MomentTemplate,
    low: float,
    max_s: float,
    ctx: ClipContext,
) -> tuple[float, list[str]]:
    """Talk and banter (speech-led moments): when the setup before the core is shorter than
    the template's minimum, the utterance just before the clip (ending within
    LEAD_IN_GAP_S) is the setup and is included, once."""
    if core.start - start >= template.min_setup_s:
        return start, []
    first = _first_utterance(ctx, start, end)
    if first is None:
        return start, []
    earlier = ctx.utterances.previous(first)
    if (
        earlier is None
        or first.start - earlier.end > LEAD_IN_GAP_S
        or earlier.start < low
        or end - earlier.start > max_s
        or earlier.start < start - template.max_setup_s
    ):
        return start, []
    return earlier.start, [f"setup line included ({earlier.id})"]


def on_to_answer(
    start: float, end: float, high: float, max_s: float, ctx: ClipContext
) -> tuple[float, list[str]]:
    """An end on a question or a connector word ("and", "but", "ki") runs on to the end of
    the next utterance when it follows within 2 s and fits."""
    utterances = ctx.utterances
    inside = [u for u in utterances.between(start, end) if u.end <= end + EDGE_TOLERANCE_S]
    if not inside:
        return end, []
    last = inside[-1]
    question = last.text.rstrip("\"') ").endswith("?")
    hanging = utterances.last_word(last) in UTTERANCE_CONNECTORS
    if not (question or hanging):
        return end, []
    following = utterances.following(last)
    if (
        following is None
        or following.start - last.end > 2.0
        or following.end > high
        or following.end - start > max_s
        or following.end - end > RESOLVE_END_EXPAND_S * 2
    ):
        return end, []
    reason = "the answer" if question else "the end of the thought"
    return following.end, [f"end runs on to {reason} ({following.id})"]


def _payoff_end(ctx: ClipContext, template: MomentTemplate, core: Core, high: float) -> float:
    """The end of the payoff burst after the core: payoff events from the core's end within
    `payoff_reach_s`, chained while they are at most RESOLVE_BURST_GAP_S apart."""
    found: list[Event] = [
        e
        for e in ctx.events
        if kind_matches(e.kind, template.payoff_kinds)
        and core.end - 1 <= e.t <= core.end + template.payoff_reach_s
        and e.t < high
    ]
    end = core.end
    for index, event in enumerate(sorted(found, key=lambda e: e.t)):
        # The first payoff may come anywhere in the reach; the rest chain as one burst.
        if index > 0 and event.t - end > RESOLVE_BURST_GAP_S:
            break
        end = max(end, min(high, event.end))
    return end


def _setup_start(
    ctx: ClipContext, template: MomentTemplate, core: Core, hints: Hints, low: float
) -> tuple[float, list[str]]:
    notes: list[str] = []
    reach = core.start - template.max_setup_s
    start = core.start - (template.min_setup_s if core.exact else 0.0)
    setup = ctx.events_of(template.setup_kinds, max(low, reach), core.start)
    if setup:
        earliest = setup[0]
        start = min(start, earliest.t - RESOLVE_SETUP_LEAD_S)
        notes.append(f"setup includes {earliest.kind} ({earliest.id})")
    if hints.setup_at is not None and reach - 10 <= hints.setup_at < core.start:
        start = min(start, hints.setup_at)
    if hints.start is not None and hints.start < start:
        # The LLM's own start, but never more than the template's setup reach.
        start = max(hints.start, min(start, reach))
    return max(low, start), notes


def _wanted_end(
    ctx: ClipContext, template: MomentTemplate, core: Core, hints: Hints, high: float
) -> tuple[float, list[str]]:
    notes: list[str] = []
    payoff = _payoff_end(ctx, template, core, high)
    if payoff > core.end + 0.5:
        notes.append("end includes the payoff")
    end = payoff + (template.reaction_s if core.exact else 0.0)
    reach = core.end + template.payoff_reach_s + template.reaction_s + RESOLVE_END_EXPAND_S
    if hints.end_at is not None and core.end <= hints.end_at <= reach:
        end = max(end, hints.end_at)
    if hints.end is not None and hints.end > end:
        # The LLM's own end (inside the segment); trailing dead air is cut afterwards.
        end = hints.end
    return min(high, end), notes


def trim_dead_air(
    start: float, end: float, core: Core, template: MomentTemplate, ctx: ClipContext
) -> tuple[float, float, list[str]]:
    """Cut more than RESOLVE_DEAD_AIR_S of nothing (no speech, no event) at either edge,
    keeping the template's minimum setup before an exact core."""
    if not ctx.observed:
        return start, end, []
    notes: list[str] = []
    keep_setup = core.start - (template.min_setup_s if core.exact else 0.0)
    if start < keep_setup and not ctx.busy(start, start + RESOLVE_DEAD_AIR_S):
        first = _first_busy(ctx, start, core.end)
        if first is not None:
            trimmed = min(keep_setup, max(start, first - RESOLVE_SETUP_LEAD_S))
            if trimmed > start + 0.5:
                notes.append(f"{trimmed - start:.0f} s of dead air cut at the start")
                start = trimmed
    if end > core.end and not ctx.busy(end - RESOLVE_DEAD_AIR_S, end):
        last = _last_busy(ctx, core.end, end)
        trimmed = max(core.end, (last if last is not None else core.end) + 1.0)
        if trimmed < end - 0.5:
            notes.append(f"{end - trimmed:.0f} s of dead air cut at the end")
            end = trimmed
    return start, end, notes


def _first_busy(ctx: ClipContext, start: float, end: float) -> float | None:
    times = [e.t for e in ctx.events if start <= e.t < end]
    speech = ctx.utterances.speech_edges(start, end)
    if speech is not None:
        times.append(speech[0])
    return min(times) if times else None


def _last_busy(ctx: ClipContext, start: float, end: float) -> float | None:
    times = [min(e.end, end) for e in ctx.events if start <= e.t < end]
    speech = ctx.utterances.speech_edges(start, end)
    if speech is not None:
        times.append(min(speech[1], end))
    return max(times) if times else None


def fit_length(
    start: float,
    end: float,
    core: Core,
    template: MomentTemplate,
    limits: Limits,
    low: float,
    high: float,
    ctx: ClipContext,
) -> tuple[float, float, list[str]]:
    """Bring the clip into [min_s, max_s] on utterance edges: too long loses setup, then
    reaction, then is cut at the last utterance end that fits; too short grows by whole
    utterances after it, then before it, then (no speech) by fixed padding. A montage
    (D-051) is too long only when its kept parts are: skipping quiet gaps comes first."""
    notes: list[str] = []
    utterances = ctx.utterances
    # Montage first: a long span whose kept parts fit is not trimmed.
    over = end - start > limits.max_s
    if over and kept_length(start, end, template, ctx, limits.min_s) <= limits.max_s:
        notes.append("quiet gaps skipped to fit the length (montage)")
        over = False
    if over:
        keep = core.start - (template.min_setup_s if core.exact else 0.0)
        target = max(start, min(keep, end - limits.max_s))
        start = snap_start(target, low, core.start, ctx)
        if end - start > limits.max_s:
            # Snapping went back to the utterance start: move on past it instead.
            inside = utterances.at(target, EDGE_TOLERANCE_S)
            if inside is not None and inside.end <= end - limits.min_s:
                start = inside.end
        notes.append("setup shortened to fit the length")
    if over and end - start > limits.max_s:
        end = max(core.end, start + limits.max_s)
        fitting = [u.end for u in utterances.between(start, end) if u.end <= start + limits.max_s]
        if fitting and fitting[-1] >= min(core.end, start + limits.min_s):
            end = fitting[-1]
        else:
            end = min(end, start + limits.max_s)
        notes.append("end cut at the last sentence that fits")
    if end - start < limits.min_s:
        for utterance in utterances.between(end, high):
            if end - start >= limits.min_s or utterance.end - start > limits.max_s:
                break
            if utterance.end > end:
                end = utterance.end
        for utterance in reversed(utterances.between(low, start)):
            if end - start >= limits.min_s or end - utterance.start > limits.max_s:
                break
            if utterance.start < start:
                start = utterance.start
        if end - start < limits.min_s:
            missing = limits.min_s - (end - start)
            end = min(high, end + missing)
            start = max(low, end - limits.min_s)
            notes.append("grown to the minimum length with plain padding")
        else:
            notes.append("grown to the minimum length by whole sentences")
    return start, end, notes


def pad(
    start: float,
    end: float,
    low: float,
    high: float,
    limits: Limits,
    ctx: ClipContext,
    template: MomentTemplate,
) -> tuple[float, float]:
    """A little room before the first and after the last word, never into a neighbouring
    word, outside the segment, or past the maximum length."""
    words = ctx.utterances.words
    if not words:
        return start, end
    before = max((w.end for w in words if w.end <= start + 1e-6), default=0.0)
    after = min((w.start for w in words if w.start >= end - 1e-6), default=ctx.duration_s)
    padded_start = max(low, before, start - CLIP_LEAD_PAD_S)
    padded_end = min(high, after, end + CLIP_TAIL_PAD_S)
    extra = (start - padded_start) + (padded_end - end)
    if kept_length(start, end, template, ctx, limits.min_s) + extra > limits.max_s:
        return start, end
    return padded_start, padded_end


def resolve(
    ctx: ClipContext,
    template: MomentTemplate,
    core: Core,
    limits: Limits,
    hints: Hints | None = None,
) -> Resolution:
    hints = hints or Hints()
    allow_cross = hints.multi_round and ctx.strategy.allow_multi_round
    low, high, core, notes = segment_bounds(ctx, core, allow_cross)
    start, setup_notes = _setup_start(ctx, template, core, hints, low)
    end, end_notes = _wanted_end(ctx, template, core, hints, high)
    notes += setup_notes + end_notes
    start = snap_start(start, low, core.start, ctx)
    start, more = back_to_setup(start, end, low, limits.max_s, ctx)
    notes += more
    if template.speech_led:
        start, more = with_lead_in(start, end, core, template, low, limits.max_s, ctx)
        notes += more
    end = snap_end(end, core.end, high, ctx)
    end, more = on_to_answer(start, end, high, limits.max_s, ctx)
    notes += more
    start, end, more = trim_dead_air(start, end, core, template, ctx)
    notes += more
    if more:
        start = snap_start(start, low, core.start, ctx)
        end = snap_end(end, core.end, high, ctx)
    start, end, more = fit_length(start, end, core, template, limits, low, high, ctx)
    notes += more
    start, end = pad(start, end, low, high, limits, ctx, template)
    return Resolution(
        start=round(start, TIME_DECIMALS),
        end=round(end, TIME_DECIMALS),
        core=core,
        template=template,
        low=low,
        high=high,
        cross_allowed=allow_cross,
        notes=notes,
    )
