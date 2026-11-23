"""The quality gate and deterministic repair (D-050): invalid times, incomplete sentences and
events, excessive length, structural violations, unrepairable clips and duplicates."""

import math

from app.pipeline.context.boundaries import Core, Limits, resolve
from app.pipeline.context.engine import ContextEngine, Moment
from app.pipeline.context.quality import HARD, Defect, repair, validate
from app.pipeline.context.strategies import GENERIC, VALORANT
from tests.context_fixtures import context, speech

LIMITS = Limits(min_s=10.0, max_s=60.0)


def defects(start: float, end: float, ctx: object, resolution: object) -> set[Defect]:
    return {i.defect for i in validate(start, end, resolution, ctx, LIMITS)}  # type: ignore[arg-type]


def sentences_ctx() -> object:
    return context(GENERIC, speech((100.0, "First we set up the trap near the door."),
                                   (106.0, "Then we wait for him to walk in."),
                                   (112.0, "And he walks straight into it."),
                                   (118.0, "Best day of my life.")))  # fmt: skip


def test_invalid_timestamps_are_rejected() -> None:
    ctx = sentences_ctx()
    resolution = resolve(ctx, GENERIC.template("moment"), Core(100.0, 120.0), LIMITS)  # type: ignore[arg-type]
    for start, end in ((math.nan, 120.0), (130.0, 120.0), (-5.0, 20.0), (590.0, 650.0)):
        assert defects(start, end, ctx, resolution) == {Defect.INVALID_TIMES}
        assert not repair(resolution, ctx, LIMITS, start, end).ok  # type: ignore[arg-type]


def test_an_incomplete_sentence_is_found_and_repaired() -> None:
    ctx = sentences_ctx()
    resolution = resolve(ctx, GENERIC.template("moment"), Core(108.0, 113.0), LIMITS)  # type: ignore[arg-type]
    assert Defect.START_MID_UTTERANCE in defects(107.0, 121.0, ctx, resolution)
    assert Defect.END_MID_UTTERANCE in defects(100.0, 113.0, ctx, resolution)
    fixed = repair(resolution, ctx, LIMITS, 107.0, 116.5)  # type: ignore[arg-type]
    assert fixed.ok, fixed.issues
    assert fixed.start == 106.0  # the sentence start
    assert fixed.end >= 114.7  # "...walks straight into it." is whole
    assert any("start_mid_utterance" in line for line in fixed.log)


def test_an_incomplete_event_gets_its_payoff() -> None:
    ctx = context(VALORANT, texts={100.0: ["BUY PHASE"], 130.0: ["KILLED SAGE"],
                                   140.0: ["ROUND WON"]})  # fmt: skip
    template = VALORANT.template("round_outcome")
    resolution = resolve(ctx, template, Core(128.0, 132.0, True), LIMITS)
    assert Defect.MISSING_PAYOFF in defects(118.0, 135.0, ctx, resolution)
    fixed = repair(resolution, ctx, LIMITS, 118.0, 135.0)
    assert fixed.end >= 142.0
    assert Defect.MISSING_PAYOFF not in {i.defect for i in fixed.issues}


def test_excessive_duration_is_repaired_on_sentence_edges() -> None:
    items = [(100.0 + 8 * i, f"This is sentence number {i} of the story.") for i in range(12)]
    ctx = context(GENERIC, speech(*items))
    resolution = resolve(ctx, GENERIC.template("story"), Core(100.0, 130.0), LIMITS)
    assert Defect.TOO_LONG in defects(100.0, 190.0, ctx, resolution)
    fixed = repair(resolution, ctx, LIMITS, 100.0, 190.0)
    assert fixed.ok
    assert fixed.end - fixed.start <= 60.0
    assert not fixed.hard


def test_a_structural_violation_is_cut_at_the_edge() -> None:
    ctx = context(VALORANT, texts={100.0: ["BUY PHASE"], 130.0: ["KILLED SAGE"],
                                   140.0: ["ROUND WON"], 156.0: ["BUY PHASE"]})  # fmt: skip
    resolution = resolve(ctx, VALORANT.template("kill"), Core(130.0, 132.0, True), LIMITS)
    assert Defect.CROSSES_BOUNDARY in defects(128.0, 175.0, ctx, resolution)
    fixed = repair(resolution, ctx, LIMITS, 128.0, 175.0)
    assert fixed.ok
    assert fixed.end <= 148.0


def test_a_core_longer_than_the_limit_cannot_be_repaired() -> None:
    ctx = sentences_ctx()
    resolution = resolve(ctx, GENERIC.template("moment"), Core(100.0, 180.0, True), LIMITS)  # type: ignore[arg-type]
    fixed = repair(resolution, ctx, LIMITS)  # type: ignore[arg-type]
    assert not fixed.ok
    assert {i.defect for i in fixed.hard} & HARD


def engine_for(words_ctx_items: list[tuple[float, str]]) -> ContextEngine:
    return ContextEngine(GENERIC, speech(*words_ctx_items), 600.0)


def test_a_high_score_never_excuses_a_broken_clip() -> None:
    engine = engine_for([(100.0, "We set the trap."), (300.0, "Something else entirely.")])
    broken = Moment(key=0, start=100.0, end=200.0, llm_score=1.0, limits=Limits(10.0, 20.0))
    assert engine.place(broken) is not None  # repaired: shortened to a valid length
    # A core that cannot fit is rejected however good the LLM said it is.
    impossible = Moment(key=1, start=100.0, end=101.0, llm_score=1.0, limits=Limits(10.0, 20.0))
    placed = engine.place(impossible)
    assert placed is None or placed.end - placed.start >= 10.0


def test_duplicates_and_overlaps_are_dropped_and_quality_beats_quantity() -> None:
    line = "the same story about the dog and the cat and the mailman again"
    engine = engine_for([(100.0, line), (200.0, line), (400.0, "a different great moment here")])
    moments = [
        Moment(key=0, start=100.0, end=115.0, llm_score=0.9, limits=LIMITS),
        Moment(key=1, start=110.0, end=125.0, llm_score=0.8, limits=LIMITS),  # overlaps 0
        Moment(key=2, start=200.0, end=215.0, llm_score=0.7, limits=LIMITS),  # same words
        Moment(key=3, start=400.0, end=415.0, llm_score=0.6, limits=LIMITS),
    ]
    chosen = engine.select(moments, count=4)
    assert [p.moment.key for p in chosen] == [0, 3]
    assert all(p.score > 0 for p in chosen)
