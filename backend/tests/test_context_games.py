"""Game-aware contextual strategies (D-050): Valorant rounds and clutches, GTA interactions,
chaos and failures, Minecraft activities, deaths, bosses and builds, and game detection."""

from app.pipeline.context.boundaries import Core, Hints, Limits, find_core, resolve
from app.pipeline.context.quality import Defect, validate
from app.pipeline.context.strategies import (
    GENERIC,
    GTA,
    MINECRAFT,
    VALORANT,
    GameStrategy,
    detect_strategy,
)
from app.pipeline.context.timeline import ClipContext, describe_context
from tests.context_fixtures import context, speech

LIMITS = Limits(min_s=10.0, max_s=60.0)


def clip(
    ctx: ClipContext, strategy: GameStrategy, start: float, end: float, kind: str
) -> tuple[float, float]:
    template = strategy.template(kind)
    result = resolve(ctx, template, find_core(ctx, template, start, end), LIMITS,
                     Hints(start=start, end=end))  # fmt: skip
    return result.start, result.end


# ---------- detection ----------


def test_the_game_is_detected_from_the_title_or_the_screen() -> None:
    assert detect_strategy("Jett to Rank 1 begins | Radiant grind") is VALORANT
    assert detect_strategy("GTA 5 RP but the cops hate me") is GTA
    assert detect_strategy("BACK AT IT AGAIN IN THE VICE CITY!!!!") is GTA
    assert detect_strategy("Minecraft Hardcore Day 100") is MINECRAFT
    assert detect_strategy("My honest podcast about startups") is GENERIC
    # No title hint: enough on-screen text decides.
    assert detect_strategy("Insane stream", texts=["BUY PHASE", "SPIKE PLANTED"]) is VALORANT
    assert detect_strategy("Insane stream", texts=["WASTED"]) is GENERIC  # one text is not enough


# ---------- Valorant ----------


def round_timeline(**extra_texts: list[str]) -> ClipContext:
    texts = {100.0: ["BUY PHASE"], 130.0: ["KILLED SAGE"], 140.0: ["ROUND WON"],
             156.0: ["BUY PHASE"]}  # fmt: skip
    texts.update({float(k): v for k, v in extra_texts.items()})
    sounds = {t: {"gunfire": 0.8} for t in (126.0, 128.0, 130.0)}
    return context(VALORANT, texts=texts, sounds=sounds)


def test_valorant_rounds_are_structural_boundaries() -> None:
    ctx = round_timeline()
    kinds = {e.kind for e in ctx.events}
    assert {"round_start", "round_end", "kill", "gunfire"} <= kinds
    edges = sorted(b.t for b in ctx.boundaries if b.kind == "round")
    # Round starts at 100 and 156; the round that ended at 140 closes 8 s later.
    assert edges == [100.0, 148.0, 156.0]
    assert ctx.structure_detected


def test_a_valorant_clip_stays_in_one_round_and_keeps_the_outcome() -> None:
    ctx = round_timeline()
    start, end = clip(ctx, VALORANT, 125.0, 170.0, "kill")
    assert 100.0 <= start <= 126.0  # a few seconds of the fight before the kill
    assert 142.0 <= end <= 148.0  # the "ROUND WON" result, not the next buy phase
    resolution = resolve(ctx, VALORANT.template("kill"), Core(130.0, 132.0, True), LIMITS)
    assert not any(i.defect == Defect.CROSSES_BOUNDARY
                   for i in validate(start, end, resolution, ctx, LIMITS))  # fmt: skip


def test_a_draft_crossing_into_the_next_round_is_cut() -> None:
    ctx = round_timeline(**{"162": ["KILLED OMEN"]})
    _, end = clip(ctx, VALORANT, 128.0, 175.0, "kill")
    assert end <= 148.0  # not into the round that starts at 156
    template = VALORANT.template("kill")
    resolution = resolve(ctx, template, Core(130.0, 132.0, True), LIMITS)
    defects = {i.defect for i in validate(128.0, 175.0, resolution, ctx, LIMITS)}
    assert Defect.CROSSES_BOUNDARY in defects


def test_a_clutch_keeps_the_situation_the_kills_and_the_round_result() -> None:
    words = speech((108.0, "it's a one v three I'm alone"))
    texts = {60.0: ["BUY PHASE"], 114.0: ["SPIKE PLANTED"], 128.0: ["KILLED OMEN"],
             134.0: ["KILLED JETT"], 140.0: ["KILLED SOVA"], 146.0: ["ROUND WON"]}  # fmt: skip
    sounds = {float(t): {"gunfire": 0.9} for t in range(126, 142, 2)}
    ctx = context(VALORANT, words, texts=texts, sounds=sounds)
    assert any(e.kind == "clutch_call" for e in ctx.events)
    assert any(e.kind == "spike_plant" for e in ctx.events)
    # The draft only has the kills; the situation (the 1v3 call, the plant) is added.
    start, end = clip(ctx, VALORANT, 126.0, 143.0, "clutch")
    assert start <= 108.0
    assert start >= 60.0  # not the whole round from the buy phase
    assert 148.0 <= end <= 154.0  # the round result and a short reaction, inside the round


def test_a_missing_round_outcome_is_flagged() -> None:
    ctx = round_timeline()
    template = VALORANT.template("round_outcome")
    resolution = resolve(ctx, template, Core(128.0, 132.0, True), LIMITS)
    defects = {i.defect for i in validate(118.0, 135.0, resolution, ctx, LIMITS)}
    assert Defect.MISSING_PAYOFF in defects
    assert resolution.end >= 142.0  # the resolver itself keeps the result


def test_multi_round_clips_only_when_the_story_needs_them() -> None:
    ctx = round_timeline(**{"160": ["KILLED OMEN"]})
    template = VALORANT.template("kill")
    core = Core(130.0, 162.0, True)
    one = resolve(ctx, template, core, LIMITS, Hints())
    assert one.core.end - one.core.start < 32.0  # the core was kept on one side
    assert not any(one.start + 0.3 < b.t < one.end - 0.3 for b in ctx.boundaries)
    two = resolve(ctx, template, core, LIMITS, Hints(multi_round=True))
    assert two.cross_allowed and two.start <= 130.0 and two.end >= 162.0
    assert not any(i.defect == Defect.CROSSES_BOUNDARY
                   for i in validate(two.start, two.end, two, ctx, LIMITS))  # fmt: skip
    # The generic strategy never allows it.
    assert not resolve(context(GENERIC), GENERIC.template("moment"), core, LIMITS,
                       Hints(multi_round=True)).cross_allowed  # fmt: skip


def test_without_round_markers_rounds_are_not_claimed() -> None:
    ctx = context(VALORANT, texts={130.0: ["KILLED SAGE"]})
    assert not ctx.structure_detected
    assert any("no round markers" in note for note in ctx.notes)
    assert "Round edges: not detected here" in describe_context(ctx)


# ---------- GTA ----------


def test_gta_funny_interaction_keeps_the_setup_and_the_laugh() -> None:
    words = speech((100.0, "Hey sir can I borrow your car for a second?"),
                   (104.2, "He just punched me in the face."))  # fmt: skip
    ctx = context(GTA, words, sounds={110.0: {"laughter": 0.9}})
    start, end = clip(ctx, GTA, 104.0, 109.0, "interaction")
    assert start <= 100.0  # the question is the setup
    assert end >= 112.0  # and the laughter is the payoff


def test_gta_chaos_keeps_how_it_started_and_the_outcome() -> None:
    words = speech((100.0, "the cops are on us drive drive"))
    sounds = {102.0: {"vehicle": 0.7}, 108.0: {"crash": 0.8}, 112.0: {"explosion": 0.9},
              116.0: {"laughter": 0.9}}  # fmt: skip
    ctx = context(GTA, words, sounds=sounds)
    assert {"chase", "crash", "explosion", "laughter"} <= {e.kind for e in ctx.events}
    start, end = clip(ctx, GTA, 110.0, 118.0, "chaos")
    assert start <= 100.0
    assert end >= 118.0


def test_gta_failure_ends_shortly_after_wasted() -> None:
    words = speech((110.0, "okay I can make this jump easily"))
    ctx = context(GTA, words, texts={120.0: ["WASTED"]}, sounds={118.0: {"crash": 0.8}})
    assert any(b.kind == "terminal" and b.t == 125.0 for b in ctx.boundaries)
    start, end = clip(ctx, GTA, 110.0, 140.0, "fail")
    assert start <= 110.0  # the attempt
    assert 122.0 <= end <= 125.0  # WASTED and a short reaction, not the respawn


def test_gta_story_event_keeps_whole_sentences() -> None:
    words = speech((100.0, "So back in the day my uncle had this old truck."),
                   (105.0, "One night it rolled into the lake by itself."),
                   (110.0, "We never found out how."))  # fmt: skip
    ctx = context(GTA, words)
    start, end = clip(ctx, GTA, 102.0, 107.0, "story")
    assert start <= 100.0
    assert end >= words[-1].end


# ---------- Minecraft ----------


def test_minecraft_discovery_keeps_the_exploring_before_it() -> None:
    words = speech((100.0, "let's go mining down here"), (108.0, "wait is that diamonds"))
    ctx = context(MINECRAFT, words, sounds={112.0: {"shouting": 0.9}})
    assert any(e.kind == "activity:mining" for e in ctx.events)
    start, end = clip(ctx, MINECRAFT, 107.0, 118.0, "discovery")
    assert start <= 100.0
    assert end >= 114.0


def test_minecraft_death_keeps_the_mob_and_ends_after_the_death_screen() -> None:
    words = speech((100.0, "there is a zombie behind me"))
    ctx = context(MINECRAFT, words, texts={106.0: ["You died!"]})
    start, end = clip(ctx, MINECRAFT, 104.0, 130.0, "death")
    assert start <= 100.0
    assert 106.0 < end <= 111.0  # the death screen, not the respawn after it


def test_minecraft_boss_encounter_keeps_the_encounter_and_the_outcome() -> None:
    words = speech((100.0, "the ender dragon is here"), (110.0, "hit the boss hit it"))
    ctx = context(MINECRAFT, words, texts={124.0: ["Advancement Made! Free the End"]})
    assert any(e.kind == "achievement" for e in ctx.events)
    start, end = clip(ctx, MINECRAFT, 108.0, 118.0, "boss")
    assert start <= 100.0
    assert end >= 126.0


def test_minecraft_building_completion_keeps_the_progress() -> None:
    words = speech((100.0, "building the roof now one block at a time"),
                   (120.0, "it's done look at the house"))  # fmt: skip
    ctx = context(MINECRAFT, words)
    start, end = clip(ctx, MINECRAFT, 119.0, 124.0, "build")
    assert start <= 100.0
    assert end >= words[-1].end


def test_minecraft_activity_context_is_listed_for_the_llm() -> None:
    words = speech((100.0, "we are exploring this cave"), (110.0, "strip mining for iron"))
    ctx = context(MINECRAFT, words)
    kinds = [e.kind for e in ctx.events]
    assert "activity:exploring" in kinds and "activity:mining" in kinds
    text = describe_context(ctx)
    assert "Game: Minecraft" in text and "activity:exploring" in text and "U0 [100.0-" in text


def test_a_banner_on_the_last_frame_of_a_draft_is_not_its_core() -> None:
    words = speech((100.0, "we hold this angle and wait for the push"),
                   (108.0, "they are coming B main right now"))  # fmt: skip
    ctx = context(VALORANT, words, texts={128.0: ["KILLED OMEN"]})
    template = VALORANT.template("kill")
    # The kill banner sits in the draft's last 3 s: the draft's talk is the core.
    assert not find_core(ctx, template, 100.0, 130.0).exact
    assert find_core(ctx, template, 100.0, 140.0).exact
    assert not find_core(ctx, template, 100.0, 140.0, events=False).exact
