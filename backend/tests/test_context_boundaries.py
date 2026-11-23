"""The contextual boundary resolver (D-050) on generic content: mid-sentence edges, setup,
payoff, dead air, length limits, dangling starts and unanswered questions."""

from app.pipeline.context.boundaries import (
    Core,
    Hints,
    Limits,
    find_core,
    resolve,
    snap_start,
)
from app.pipeline.context.strategies import GENERIC
from app.pipeline.context.timeline import ClipContext
from tests.context_fixtures import context, say, speech

LIMITS = Limits(min_s=15.0, max_s=60.0)


def draft(
    ctx: ClipContext, start: float, end: float, kind: str = "moment", limits: Limits = LIMITS
) -> tuple[float, float]:
    """Resolve a draft window without LLM ids, as the engine does."""
    template = GENERIC.template(kind)
    core = find_core(ctx, template, start, end)
    result = resolve(ctx, template, core, limits, Hints(start=start, end=end))
    return result.start, result.end


def test_a_start_mid_sentence_moves_to_the_sentence_start() -> None:
    ctx = context(GENERIC, speech((100.0, "Let me tell you what happened at the store."),
                                  (106.0, "It was crazy, a man walked in with a goat."),
                                  (114.0, "Nobody said anything.")))  # fmt: skip
    start, end = draft(ctx, 102.0, 116.0)
    assert 99.8 <= start <= 100.0
    assert end >= 114.0 + 0.4 * 2 + 0.3  # the last sentence is whole


def test_an_end_mid_sentence_moves_to_the_sentence_end() -> None:
    words = speech((100.0, "So the plan was simple and we had it all ready."),
                   (106.0, "We go in and then he just walked away from me."))  # fmt: skip
    ctx = context(GENERIC, words)
    start, end = draft(ctx, 100.0, 108.0, limits=Limits(5.0, 60.0))
    last = words[-1]
    assert last.end <= end <= last.end + 0.3
    assert start <= 100.0


def test_a_start_far_inside_an_utterance_before_the_core_moves_past_it() -> None:
    ctx = context(GENERIC, say(100.0, " ".join(f"w{i}" for i in range(34))))
    utterance = ctx.utterances.utterances[0]
    # Close to its start: back to the start. Far inside and before the core: after it.
    assert snap_start(102.0, 0.0, 130.0, ctx) == utterance.start
    assert snap_start(112.0, 0.0, 130.0, ctx) == utterance.end
    # Far inside but the utterance holds the core: back to its start.
    assert snap_start(112.0, 0.0, 112.0, ctx) == utterance.start


def test_missing_setup_before_a_kill_is_added() -> None:
    ctx = context(GENERIC, texts={100.0: ["KILLED REYNA"]})
    template = GENERIC.template("play")
    core = find_core(ctx, template, 100.5, 125.0)
    assert core.exact and core.start == 100.0
    start, end = draft(ctx, 100.5, 125.0, "play")
    assert start <= 100.0 - template.min_setup_s + 0.01
    # Nothing happens after the kill: the 23 s tail is cut to the minimum length.
    assert end - start == 15.0


def test_a_missed_payoff_is_included() -> None:
    words = speech((100.0, "I asked him why he did it."), (104.0, "He said because he is a cat."))
    ctx = context(GENERIC, words, sounds={110.0: {"laughter": 0.9}})
    start, end = draft(ctx, 100.0, 108.0, "funny")
    assert end >= 112.0  # the laughter at 110-112 s is the payoff
    assert start <= 100.0


def test_excessive_context_and_dead_air_are_cut() -> None:
    ctx = context(GENERIC, speech((140.0, "Okay this is the part you wanted to see."),
                                  (145.0, "Watch this, it is the best one.")))  # fmt: skip
    start, end = draft(ctx, 100.0, 160.0)
    assert 135.0 <= start <= 140.0
    assert end <= 156.0
    assert end - start >= 15.0


def test_too_long_is_cut_at_a_sentence_end() -> None:
    items = [(100.0 + 10 * i, f"Sentence number {i} is right here.") for i in range(10)]
    ctx = context(GENERIC, speech(*items))
    start, end = draft(ctx, 100.0, 195.0)
    ends = [u.end for u in ctx.utterances.utterances]
    assert end - start <= 60.0
    assert any(abs(end - (e + 0.25)) < 0.01 or abs(end - e) < 0.01 for e in ends)


def test_too_short_grows_by_whole_sentences() -> None:
    items = [(100.0 + 6 * i, "Short line here.") for i in range(5)]
    ctx = context(GENERIC, speech(*items))
    start, end = draft(ctx, 100.0, 101.5)
    assert end - start >= 15.0
    ends = [u.end for u in ctx.utterances.utterances]
    assert any(abs(end - e) <= 0.25 for e in ends)


def test_a_dangling_start_moves_back_to_the_setup() -> None:
    ctx = context(GENERIC, speech((100.0, "What do you think about remote work?"),
                                  (106.0, "So honestly I think it is great for focus."),
                                  (112.0, "You get so much more done.")))  # fmt: skip
    start, _ = draft(ctx, 106.0, 125.0, "conversation")
    assert start <= 100.0


def test_an_unanswered_question_runs_on_to_the_answer() -> None:
    words = speech(
        (100.0, "We tried everything this season."),
        (118.0, "Do you want to know the secret?"),
        (121.5, "Practice every day."),
    )
    ctx = context(GENERIC, words)
    _, end = draft(ctx, 100.0, 120.6)
    assert end >= words[-1].end


def test_without_any_evidence_the_draft_stays() -> None:
    ctx = context(GENERIC)
    ctx.observed = False
    assert draft(ctx, 100.0, 130.0) == (100.0, 130.0)


def test_a_core_never_leaves_its_scene() -> None:
    ctx = context(GENERIC, texts={120.0: ["GAME OVER"]})
    template = GENERIC.template("moment")
    result = resolve(ctx, template, Core(100.0, 118.0), LIMITS, Hints(start=100.0, end=140.0))
    assert result.end <= 120.0
