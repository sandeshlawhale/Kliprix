"""The contextual engine end to end (D-050): the clip critic and its limits, and the review
round answering with context ids that the backend turns into clip edges."""

import re
from typing import Any

from app.core.errors import AppError, ErrorCode
from app.llm.schemas import (
    CritiqueAnswer,
    CritiqueClip,
    CritiqueRepair,
    LlmClip,
    ReviewAnswer,
    ReviewClip,
    SelectionAnswer,
    TitleAnswer,
)
from app.pipeline.candidates import Candidate, SignalScores
from app.pipeline.context.boundaries import Limits
from app.pipeline.context.engine import ContextEngine, Moment
from app.pipeline.context.quality import Defect
from app.pipeline.context.strategies import VALORANT
from app.pipeline.review import ClipAnalysis, build_rows, zone_of
from app.pipeline.screen_text import ScreenText
from app.pipeline.select import VideoFacts, select_more
from app.pipeline.sound import SoundEvents
from app.pipeline.transcribe import Segment, TranscriptFile, Word
from tests.context_fixtures import speech

LIMITS = Limits(min_s=10.0, max_s=60.0)


class CriticLlm:
    def __init__(self, answer: CritiqueAnswer | None = None, fail: bool = False) -> None:
        self.answer = answer
        self.fail = fail
        self.users: list[str] = []

    def complete_json(self, system: str, user: str, schema: object, *_a: Any, **_k: Any) -> object:
        assert schema is CritiqueAnswer
        self.users.append(user)
        if self.fail:
            raise AppError(ErrorCode.LLM_RATE_LIMITED, "slow down")
        return self.answer


def clutch_engine() -> ContextEngine:
    """Valorant talk without any kill or round evidence: a clutch whose payoff the rules
    cannot see (sent to the critic)."""
    words = speech(
        (100.0, "okay it is a one v two now"),
        (104.0, "I am going to wait behind this box"),
        (110.0, "he peeks and I hit him"),
        (115.0, "and the last one is going for the bomb"),
        (122.0, "no way what a moment that was"),
    )
    return ContextEngine(VALORANT, words, 600.0)


def test_the_critic_moves_an_edge_by_id_and_unknown_ids_are_ignored() -> None:
    engine = clutch_engine()
    target = engine.utterances.utterances[4]
    answer = CritiqueAnswer(clips=[CritiqueClip(
        id=0, valid=False, issues=["ending cuts off reaction"],
        repairs=[CritiqueRepair(action="extend_end_to", ref=target.id),
                 CritiqueRepair(action="extend_end_to", ref="U99"),
                 CritiqueRepair(action="teleport", ref="U1")],
    )])  # fmt: skip
    llm = CriticLlm(answer)
    moment = Moment(key=0, start=100.0, end=118.0, llm_score=0.8, limits=LIMITS,
                    moment_type="clutch")  # fmt: skip
    [placed] = engine.select([moment], 1, client=llm, title="Valorant ranked")  # type: ignore[arg-type]
    assert Defect.PAYOFF_UNVERIFIED in placed.defects
    assert (
        len(llm.users) == 1 and "U4 [" in llm.users[0] and "Rule findings: payoff" in llm.users[0]
    )
    assert placed.end >= target.end
    assert "edges moved by the critic" in placed.notes
    assert "critic: ending cuts off reaction" in placed.notes


def test_the_critic_cannot_bypass_the_rules() -> None:
    # "next round" at 118 s starts a new round: a structural edge.
    words = speech((100.0, "okay it is a one v two now"), (106.0, "wait for it"),
                   (118.0, "next round now lets go"))  # fmt: skip
    engine = ContextEngine(VALORANT, words, 600.0)
    after = engine.utterances.utterances[2]
    answer = CritiqueAnswer(clips=[CritiqueClip(id=0, repairs=[
        CritiqueRepair(action="extend_end_to", ref=after.id),
    ])])  # fmt: skip
    moment = Moment(key=0, start=100.0, end=112.0, llm_score=0.8, limits=LIMITS,
                    moment_type="clutch")  # fmt: skip
    llm = CriticLlm(answer)
    [placed] = engine.select([moment], 1, client=llm)  # type: ignore[arg-type]
    assert len(llm.users) == 1
    # The move was repaired back to the round edge: never past it.
    assert placed.end <= 118.0
    assert Defect.CROSSES_BOUNDARY not in placed.defects


def test_the_critic_can_drop_a_clip_that_is_not_worth_sharing() -> None:
    engine = clutch_engine()
    answer = CritiqueAnswer(clips=[CritiqueClip(id=0, valid=False, worth_sharing=False,
                                                issues=["nothing happens"])])  # fmt: skip
    moments = [
        Moment(key=0, start=100.0, end=114.0, llm_score=0.9, limits=LIMITS, moment_type="clutch"),
        Moment(key=1, start=300.0, end=320.0, llm_score=0.5, limits=LIMITS, moment_type="funny"),
    ]
    chosen = engine.select(moments, 2, client=CriticLlm(answer))  # type: ignore[arg-type]
    assert [p.moment.key for p in chosen] == [1]


def test_a_failed_critic_changes_nothing_and_clean_clips_skip_it() -> None:
    engine = clutch_engine()
    moment = Moment(key=0, start=100.0, end=118.0, llm_score=0.8, limits=LIMITS,
                    moment_type="clutch")  # fmt: skip
    llm = CriticLlm(fail=True)
    [placed] = engine.select([moment], 1, client=llm)  # type: ignore[arg-type]
    assert len(llm.users) == 1 and placed.start <= 100.0
    quiet = CriticLlm()
    funny = Moment(key=0, start=100.0, end=118.0, llm_score=0.8, limits=LIMITS,
                   moment_type="funny")  # fmt: skip
    engine.select([funny], 1, client=quiet)  # type: ignore[arg-type]
    assert quiet.users == []


# ---------- the review round with context ids ----------


FACTS = VideoFacts(title="Valorant ranked: Jett to Radiant", channel=None, duration_s=1000.0)
WORDS = speech((400.0, "it is a one v three I am alone"), (418.0, "let's go what a round"))
TEXTS = {404.0: ["SPIKE PLANTED"], 412.0: ["KILLED OMEN"], 414.0: ["KILLED JETT"],
         416.0: ["KILLED SOVA"], 420.0: ["ROUND WON"], 436.0: ["BUY PHASE"]}  # fmt: skip


def analysis(clip_id: int, start: float, end: float) -> ClipAnalysis:
    zone_start, zone_end = zone_of(start, end, FACTS.duration_s)
    frames: dict[str, list[str]] = {
        f"{t:.2f}": [] for t in range(int(zone_start), int(zone_end), 4)
    }
    frames.update({f"{t:.2f}": v for t, v in TEXTS.items()})
    sounds = SoundEvents(window_s=2, windows=[{} for _ in range(500)])
    rows = build_rows(zone_start, zone_end, [], sounds, ScreenText(frames=frames), WORDS)
    return ClipAnalysis(id=clip_id, start=start, end=end, zone_start=zone_start,
                        zone_end=zone_end, rows=rows, scene_ends=[], actions=[])  # fmt: skip


class PipelineLlm:
    def __init__(self) -> None:
        self.review_user = ""
        self.review_system = ""

    def complete_json(self, system: str, user: str, schema: object, *_a: Any, **_k: Any) -> object:
        if schema is SelectionAnswer:
            # Round 1 drafts a clip that starts on the first kill and ends before the result.
            return SelectionAnswer(clips=[LlmClip(candidate_id=0, start=412.0, end=419.0,
                                                  title="Clutch", score=0.8)])  # fmt: skip
        if schema is ReviewAnswer:
            self.review_user, self.review_system = user, system
            kills = re.findall(r"^(E\d+) \[\d+-\d+ s\] kill:", user, re.MULTILINE)
            setup = re.search(r"^(E\d+) \[\d+-\d+ s\] clutch_call:", user, re.MULTILINE)
            result = re.search(r"^(E\d+) \[\d+-\d+ s\] round_end:", user, re.MULTILINE)
            return ReviewAnswer(clips=[ReviewClip(
                id=0, moment_type="clutch", core=kills,
                setup=setup.group(1) if setup else None,
                payoff=result.group(1) if result else None, title="I caught all three",
                score=0.9,
            )])  # fmt: skip
        if schema is CritiqueAnswer:
            return CritiqueAnswer(clips=[])
        return TitleAnswer(clips=[])


def test_the_review_names_the_moment_by_id_and_the_backend_builds_the_clip() -> None:
    words: list[Word] = WORDS
    transcript = TranscriptFile(
        language="en", spans=[(0.0, 1000.0)],
        segments=[Segment(start=400.0, end=430.0, text="speech", words=words)],
    )  # fmt: skip
    candidates = [Candidate(peak_t=414.0, start=395.0, end=425.0, score=0.9,
                            signals=SignalScores(audio=0.5))]  # fmt: skip
    llm = PipelineLlm()
    [clip] = select_more(
        FACTS, candidates, transcript, [], count=1, extended=True,
        client=llm, reviewer=analysis,  # type: ignore[arg-type]
    )  # fmt: skip
    # The prompt carried the ids, the game, and the round edges.
    assert "Utterances:" in llm.review_user and "Events:" in llm.review_user
    assert (
        "Game: Valorant" in llm.review_user
        and "Round edges (from on-screen text)" in llm.review_user
    )
    assert "Valorant is played in rounds" in llm.review_system
    # The clip: the 1v3 call and the plant (setup), all three kills, the round result.
    assert clip.start <= 400.0
    assert clip.end >= 422.0
    assert clip.end <= 428.0  # inside the round (it ended at 420 s; edge 8 s later)
    assert clip.moment_type == "clutch"
    assert clip.title == "I caught all three"
    assert any("core from the LLM's ids" in note for note in clip.quality)


def test_reviewer_dropped_drafts_fill_the_open_slots() -> None:
    """D-060: the user gets the count they asked for; kept drafts still come first."""
    engine = clutch_engine()
    moments = [
        Moment(key=0, start=100.0, end=118.0, llm_score=0.6, limits=LIMITS, group=0),
        Moment(key=1, start=300.0, end=320.0, llm_score=0.9, limits=LIMITS, group=2),
        Moment(key=2, start=400.0, end=420.0, llm_score=0.8, limits=LIMITS, group=2),
    ]
    assert [p.moment.key for p in engine.select(moments, 1)] == [0]
    assert [p.moment.key for p in engine.select(moments, 3)] == [0, 1, 2]
