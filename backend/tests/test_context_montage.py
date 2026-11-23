"""Montage clips (D-051): quiet gaps inside an action moment are skipped, a far setup line is
joined in front, lengths count only what plays, and the part time helpers map captions."""

from app.pipeline.context.boundaries import Core, Hints, Limits, resolve
from app.pipeline.context.engine import ContextEngine, Moment
from app.pipeline.context.montage import plan, quiet_gaps
from app.pipeline.context.quality import Defect, validate
from app.pipeline.context.strategies import GTA, VALORANT
from app.pipeline.parts import effective, normalized, to_output, to_source, total, words_for
from app.pipeline.screen_text import ScreenText
from app.pipeline.sound import SoundEvents
from app.pipeline.transcribe import Word
from tests.context_fixtures import DURATION, context, speech

LIMITS = Limits(min_s=10.0, max_s=60.0)
KILLS = {110.0: ["KILLED SAGE"], 140.0: ["KILLED OMEN"], 168.0: ["KILLED JETT"]}


def round_words() -> list[Word]:
    """Talk before the first kill and after the last; silence between the kills."""
    return speech(
        (100.0, "okay we go B this round"),
        (111.0, "one down"),
        (141.0, "two"),
        (169.0, "three that is the round lets go"),
    )


def test_quiet_gaps_between_kills_are_skipped_with_lead_and_tail() -> None:
    ctx = context(VALORANT, round_words(), texts=KILLS)
    gaps = quiet_gaps(98.0, 175.0, ctx)
    # The walk from the round call to the first kill, and the waits between the kills.
    assert len(gaps) == 3
    for skip_start, skip_end in gaps:
        assert not ctx.busy(skip_start - 0.9, skip_end + 1.4)
        assert skip_end - skip_start >= 3.0
    parts = plan(98.0, 175.0, VALORANT.template("multi_kill"), ctx, LIMITS.min_s)
    assert len(parts) == 4
    assert parts[0][0] == 98.0 and parts[-1][1] == 175.0
    # Every kill stays inside a part, and the montage is much shorter than the round.
    for t in KILLS:
        assert any(a <= t < b for a, b in parts)
    assert total(parts) < 35.0


def test_talk_moments_are_never_cut_up() -> None:
    ctx = context(VALORANT, round_words(), texts=KILLS)
    assert plan(98.0, 175.0, VALORANT.template("funny"), ctx) == ()
    assert plan(98.0, 175.0, VALORANT.template("conversation"), ctx) == ()


def test_a_gap_with_speech_is_kept() -> None:
    words = round_words() + speech((120.0, "rotate rotate they are coming"), (152.0, "careful"))
    ctx = context(VALORANT, words, texts=KILLS)
    parts = plan(98.0, 175.0, VALORANT.template("multi_kill"), ctx)
    for word in words:
        if 98.0 <= word.start < 175.0:
            assert any(a <= word.start and word.end <= b for a, b in parts), word


def test_a_long_round_fits_by_skipping_instead_of_losing_the_last_kill() -> None:
    ctx = context(VALORANT, round_words(), texts=KILLS)
    template = VALORANT.template("multi_kill")
    core = Core(110.0, 169.0, exact=True)
    resolution = resolve(ctx, template, core, LIMITS, Hints(start=99.0, end=175.0))
    # The span is longer than 60 s, but what plays fits, so the third kill stays.
    assert resolution.end - resolution.start > LIMITS.max_s
    assert resolution.end >= 172.0
    assert any("montage" in note for note in resolution.notes)
    issues = validate(resolution.start, resolution.end, resolution, ctx, LIMITS)
    assert Defect.TOO_LONG not in {i.defect for i in issues}


def test_without_a_montage_the_same_span_is_too_long() -> None:
    ctx = context(VALORANT, round_words(), texts=KILLS)
    template = VALORANT.template("conversation")
    resolution = resolve(ctx, template, Core(100.0, 175.0, exact=True), LIMITS)
    issues = validate(99.0, 175.0, resolution, ctx, LIMITS)
    assert Defect.TOO_LONG in {i.defect for i in issues}


def gta_engine(texts: dict[float, list[str]] | None = None) -> ContextEngine:
    words = speech(
        (40.0, "watch I am going to rob this bank"),
        (150.0, "go go go get the money"),
        (158.0, "they are shooting at us"),
        (170.0, "we made it bro"),
    )
    windows: list[dict[str, float]] = [{} for _ in range(int(DURATION // 2))]
    for t in (152.0, 156.0, 160.0, 164.0):
        windows[int(t // 2)] = {"gunfire": 0.9}
    frames: dict[str, list[str]] = {f"{t:.2f}": [] for t in range(0, int(DURATION), 4)}
    for t, items in (texts or {}).items():
        frames[f"{t:.2f}"] = items
    return ContextEngine(
        GTA,
        words,
        DURATION,
        events=SoundEvents(window_s=2, windows=windows),
        text=ScreenText(frames=frames),
    )


def test_a_setup_said_minutes_before_is_joined_as_the_first_part() -> None:
    engine = gta_engine()
    moment = Moment(
        key=0, start=148.0, end=175.0, llm_score=0.8, limits=LIMITS, moment_type="chaos",
        setup_at=40.0,
    )  # fmt: skip
    placed = engine.place(moment)
    assert placed is not None
    first = placed.parts[0]
    assert 39.5 <= first[0] <= 40.0 and 42.0 <= first[1] < 45.0
    assert placed.parts[1][0] == placed.start
    assert any("joined" in note for note in placed.notes)
    assert total(placed.parts) <= LIMITS.max_s


def test_a_setup_is_not_joined_across_a_failure_screen() -> None:
    engine = gta_engine(texts={90.0: ["WASTED"]})
    moment = Moment(
        key=0, start=148.0, end=175.0, llm_score=0.8, limits=LIMITS, moment_type="chaos",
        setup_at=40.0,
    )  # fmt: skip
    placed = engine.place(moment)
    assert placed is not None
    assert all(a >= 100.0 for a, _ in placed.parts)
    assert any("not joined" in note for note in placed.notes)


def test_part_times_map_both_ways_and_words_follow_the_parts() -> None:
    parts = ((10.0, 20.0), (50.0, 55.0))
    assert to_output(15.0, parts) == 5.0
    assert to_output(52.0, parts) == 12.0
    assert to_output(30.0, parts) is None
    assert to_source(12.0, parts) == 52.0
    words = [
        Word(text="a", start=11.0, end=11.5),
        Word(text="gone", start=30.0, end=30.4),
        Word(text="b", start=51.0, end=51.4),
    ]
    mapped = words_for(words, parts)
    assert [w.text for w in mapped] == ["a", "b"]
    assert (mapped[1].start, mapped[1].end) == (11.0, 11.4)


def test_trimming_a_montage_keeps_its_inner_cuts() -> None:
    parts = ((10.0, 20.0), (50.0, 55.0), (80.0, 90.0))
    assert effective(12.0, 85.0, parts) == ((12.0, 20.0), (50.0, 55.0), (80.0, 85.0))
    # Trimmed down to one part: an ordinary clip again.
    assert effective(12.0, 19.0, parts) == ((12.0, 19.0),)
    assert normalized([(5.0, 6.0), (1.0, 2.0), (1.5, 3.0)]) == ((1.0, 3.0), (5.0, 6.0))


def test_later_kills_of_the_round_chain_into_the_clip() -> None:
    """The first kill is the core; the next kills 30 s and 28 s later join the clip and the
    waits between them are skipped, so the round fits."""
    ctx = context(VALORANT, round_words(), texts=KILLS)
    template = VALORANT.template("kill")
    resolution = resolve(ctx, template, Core(110.0, 111.0, exact=True), LIMITS)
    assert resolution.end >= 169.0
    parts = plan(resolution.start, resolution.end, template, ctx, LIMITS.min_s)
    assert len(parts) >= 3 and total(parts) <= LIMITS.max_s
    assert all(any(a <= t < b for a, b in parts) for t in KILLS)


def test_talk_payoffs_do_not_chain_far() -> None:
    ctx = context(VALORANT, round_words(), texts=KILLS)
    template = VALORANT.template("funny")
    resolution = resolve(ctx, template, Core(110.0, 111.0, exact=True), LIMITS)
    # Only grown to the minimum length; the last kill (168 s) is not chained in.
    assert resolution.end < 160.0
