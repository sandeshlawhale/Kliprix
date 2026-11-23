"""Context reconstruction (D-050): what happens around a clip, as typed events, structural
boundaries and activities, built from the same 2 s timeline rows the review reads (D-046).

Rows come from the close analysis of a draft when there is one (screen type, sounds, every
keyframe's text, speech); otherwise they are built from what the pipeline already has for the
whole video (screen type samples, sound events, the candidates' on-screen text, the
transcript), so no video is read again here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import count

import numpy as np

from app.config import (
    CONTEXT_ZONE_S,
    REVIEW_DULL_MIN,
    REVIEW_DULL_ROWS,
    REVIEW_STEP_S,
    ROUND_START_MERGE_S,
    TERMINAL_MERGE_S,
)
from app.llm.prompts import clock
from app.pipeline.context.strategies import GameStrategy
from app.pipeline.context.utterances import UtteranceIndex
from app.pipeline.review import Row, build_rows, scene_ends
from app.pipeline.screen import ScreenFile, dull_share, window_shares
from app.pipeline.screen_text import ScreenText
from app.pipeline.sound import EVENT_NAMES, SoundEvents

# Display names in review rows -> sound group names.
_GROUP_OF = {name: group for group, name in EVENT_NAMES.items()}


@dataclass(frozen=True)
class Event:
    id: str
    t: float
    end: float
    kind: str
    label: str
    source: str  # "text", "speech", "sound", "screen"


@dataclass(frozen=True)
class Boundary:
    """A structural edge a clip must not cross (unless the story needs it, `multi_round`)."""

    t: float
    kind: str  # "round", "scene", "terminal"
    reason: str


@dataclass
class ClipContext:
    """Everything the resolver and the validator know around one clip."""

    zone_start: float
    zone_end: float
    duration_s: float
    strategy: GameStrategy
    utterances: UtteranceIndex
    rows: list[Row]
    events: list[Event]
    boundaries: list[Boundary]
    # True when at least one reader saw this zone (screen, sounds, text or speech): only then
    # does "nothing here" mean dead air rather than "not known".
    observed: bool
    notes: list[str] = field(default_factory=list)

    def event(self, event_id: str) -> Event | None:
        key = event_id.strip().upper()
        return next((e for e in self.events if e.id == key), None)

    def events_of(self, kinds: frozenset[str], start: float, end: float) -> list[Event]:
        return [e for e in self.events if start <= e.t < end and kind_matches(e.kind, kinds)]

    def busy(self, start: float, end: float) -> bool:
        """Speech or an event in [start, end)."""
        return self.utterances.has_speech(start, end) or any(
            e.end > start and e.t < end for e in self.events
        )

    @property
    def structure_detected(self) -> bool:
        return any(b.kind == "round" for b in self.boundaries) or any(
            e.kind in self.strategy.round_start_kinds | self.strategy.round_end_kinds
            for e in self.events
        )


def kind_matches(kind: str, kinds: frozenset[str]) -> bool:
    """`kinds` may hold "activity:*" for any activity."""
    return kind in kinds or (kind.startswith("activity:") and "activity:*" in kinds)


class EventIds:
    """Event ids unique across all contexts of one selection ("E0", "E1", ...)."""

    def __init__(self) -> None:
        self._next = count()

    def new(self) -> str:
        return f"E{next(self._next)}"


def rows_from_evidence(
    zone_start: float,
    zone_end: float,
    *,
    screen: ScreenFile | None,
    events: SoundEvents | None,
    text: ScreenText | None,
    utterances: UtteranceIndex,
) -> list[Row]:
    """Timeline rows for a zone without a close analysis."""
    screens: list[tuple[str, float]] = []
    if screen is not None:
        for t in np.arange(zone_start, zone_end, REVIEW_STEP_S):
            shares = window_shares(screen, float(t), float(t) + screen.step_s)
            group = max(shares, key=lambda g: shares[g]) if shares else ""
            screens.append((group, dull_share(shares) if shares else 0.0))
    return build_rows(zone_start, zone_end, screens, events, text, utterances.words)


def _row_text(row: Row) -> tuple[str, str]:
    return " ".join(row.text).lower(), "".join(row.text).lower()


def detect_events(rows: Sequence[Row], strategy: GameStrategy, ids: EventIds) -> list[Event]:
    """Typed events per row from the strategy's rules; runs of one kind on consecutive rows
    are one event (a gunfight, a long banner)."""
    raw: list[tuple[float, str, str, str]] = []
    for row in rows:
        spaced, joined = _row_text(row)
        speech = row.speech.lower()
        for rule in strategy.rules:
            if rule.source == "text":
                found = rule.pattern.search(spaced) or rule.pattern.search(joined)
                if found is not None:
                    shown = next((t for t in row.text if rule.pattern.search(t.lower())), None)
                    raw.append((row.t, rule.kind, f'"{shown or found.group(0).upper()}"', "text"))
            elif row.speech:
                found = rule.pattern.search(speech)
                if found is not None:
                    raw.append((row.t, rule.kind, f'said "{found.group(0)}"', "speech"))
        for name in row.sounds:
            kind = strategy.sound_kinds.get(_GROUP_OF.get(name, name))
            if kind is not None:
                raw.append((row.t, kind, name, "sound"))
        for activity, pattern in strategy.activity_rules:
            if row.speech and pattern.search(speech):
                raw.append((row.t, f"activity:{activity}", activity, "speech"))
    raw.sort(key=lambda item: (item[1], item[0]))
    merged: list[tuple[float, float, str, str, str]] = []
    for t, kind, label, source in raw:
        last = merged[-1] if merged else None
        if last is not None and last[2] == kind and t - last[1] <= REVIEW_STEP_S + 1e-6:
            merged[-1] = (last[0], t + REVIEW_STEP_S, kind, last[3], last[4])
            continue
        merged.append((t, t + REVIEW_STEP_S, kind, label, source))
    merged.sort(key=lambda item: (item[0], item[2]))
    return [
        Event(id=ids.new(), t=t, end=end, kind=kind, label=label, source=source)
        for t, end, kind, label, source in merged
    ]


def structural_boundaries(
    rows: Sequence[Row], events: Sequence[Event], strategy: GameStrategy
) -> list[Boundary]:
    """Edges a clip must not cross: round starts, `post_round_s` after a round end (or the
    next start, if sooner), `terminal_tail_s` after a terminal event; for the generic strategy
    also the scene ends of D-046 (menu runs, round words)."""
    found: list[Boundary] = []
    # One buy phase shows several banners ("BUY PHASE", "ROUND 6", "PRESS TO BUY"): a round
    # start within ROUND_START_MERGE_S of the previous one is the same start.
    starts: list[float] = []
    for t in sorted(e.t for e in events if e.kind in strategy.round_start_kinds):
        if not starts or t - starts[-1] > ROUND_START_MERGE_S:
            starts.append(t)
    for t in starts:
        found.append(Boundary(t, "round", "a new round starts"))
    last_terminal: dict[str, float] = {}
    for event in events:
        if event.kind in strategy.terminal_kinds:
            # A death banner stays on screen for a while: one terminal per TERMINAL_MERGE_S.
            previous = last_terminal.get(event.kind)
            last_terminal[event.kind] = event.t
            if previous is not None and event.t - previous <= TERMINAL_MERGE_S:
                continue
        if event.kind in strategy.round_end_kinds:
            later = [s for s in starts if s > event.t]
            edge = min([event.t + strategy.post_round_s, *later])
            found.append(Boundary(edge, "round", f"the round ended at {clock(event.t)}"))
        elif event.kind in strategy.terminal_kinds:
            edge = event.t + strategy.terminal_tail_s
            found.append(Boundary(edge, "terminal", f"{event.label} at {clock(event.t)}"))
    if strategy.generic_scene_ends:
        found += [Boundary(t, "scene", why) for t, why in scene_ends(rows)]
    elif not strategy.detects_rounds:
        # A long menu/black run still ends a scene in games without rounds (but a menu
        # overlay that comes and goes, like GTA's phone, does not: REVIEW_DULL_ROWS * 2).
        run = 0
        for row in rows:
            run = run + 1 if row.dull >= REVIEW_DULL_MIN else 0
            if run == REVIEW_DULL_ROWS * 2:
                start = row.t - (run - 1) * REVIEW_STEP_S
                found.append(Boundary(start, "scene", "menu or black screen"))
    found.sort(key=lambda b: b.t)
    result: list[Boundary] = []
    for boundary in found:
        if result and boundary.t - result[-1].t < REVIEW_STEP_S:
            continue
        result.append(boundary)
    return result


def build_context(
    zone_start: float,
    zone_end: float,
    duration_s: float,
    rows: list[Row],
    strategy: GameStrategy,
    utterances: UtteranceIndex,
    ids: EventIds,
) -> ClipContext:
    events = detect_events(rows, strategy, ids)
    boundaries = structural_boundaries(rows, events, strategy)
    observed = any(r.screen or r.sounds or r.text for r in rows) or utterances.has_speech(
        zone_start, zone_end
    )
    ctx = ClipContext(
        zone_start=zone_start,
        zone_end=zone_end,
        duration_s=duration_s,
        strategy=strategy,
        utterances=utterances,
        rows=rows,
        events=events,
        boundaries=boundaries,
        observed=observed,
    )
    if strategy.detects_rounds and not ctx.structure_detected:
        ctx.notes.append("no round markers found here; round edges are not known")
    return ctx


def zone_for(start: float, end: float, duration_s: float) -> tuple[float, float]:
    return float(max(0, int(start - CONTEXT_ZONE_S))), min(duration_s, end + CONTEXT_ZONE_S)


def describe_context(ctx: ClipContext, limit: int = 40) -> str:
    """The ids block the LLM answers with: utterances and events of the zone, and what is
    known about the structure (D-050)."""
    lines = [f"Game: {ctx.strategy.label}"]
    if ctx.strategy.detects_rounds:
        rounds = [b for b in ctx.boundaries if b.kind == "round"]
        if rounds:
            lines.append(
                "Round edges (from on-screen text): "
                + "; ".join(f"{b.t:.0f} s ({b.reason})" for b in rounds)
            )
        else:
            lines.append("Round edges: not detected here (do not claim a round change)")
    others = [b for b in ctx.boundaries if b.kind != "round"]
    if others:
        lines.append("Scene edges: " + "; ".join(f"{b.t:.0f} s ({b.reason})" for b in others))
    utterances = ctx.utterances.between(ctx.zone_start, ctx.zone_end)
    if utterances:
        lines.append("Utterances:")
        lines += [f'{u.id} [{u.start:.1f}-{u.end:.1f} s] "{u.text}"' for u in utterances[:limit]]
    if ctx.events:
        lines.append("Events:")
        lines += [
            f"{e.id} [{e.t:.0f}-{e.end:.0f} s] {e.kind}: {e.label} ({e.source})"
            for e in ctx.events[:limit]
        ]
    return "\n".join(lines)
