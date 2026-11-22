"""Two-round selection (D-046): the close analysis of a draft, its timeline, scene cuts, and
round 2 keeping, fixing and dropping drafts."""

from pathlib import Path
from typing import Any

import pytest

from app.core.errors import AppError, ErrorCode
from app.llm.client import user_content
from app.llm.schemas import ReviewAnswer, ReviewClip, SelectionAnswer, TitleAnswer, TitleText
from app.pipeline.candidates import Candidate, SignalScores
from app.pipeline.review import (
    ClipAnalysis,
    Row,
    VideoReaders,
    analyse,
    build_rows,
    describe,
    ocr_times,
    scene_ends,
)
from app.pipeline.screen_text import ScreenText
from app.pipeline.select import (
    SelectionClip,
    VideoFacts,
    cut_at_scene_end,
    draft_count,
    review_clips,
    run_select,
)
from app.pipeline.sound import SoundEvents
from app.pipeline.transcribe import Segment, TranscriptFile, Word

FACTS = VideoFacts(title="Jett to Rank 1", channel=None, duration_s=1517.0)


def test_rows_mark_scene_ends_and_actions() -> None:
    # The owner's first bad clip: quiet inside, death screen and a kill just after the end.
    events = SoundEvents(window_s=2, windows=[{} for _ in range(760)])
    events.windows[1496 // 2] = {"gunfire": 0.6}
    text = ScreenText(frames={"1500": ["KILLED BY HUSH"], "1480": ["In C Main"]})
    words = [
        Word(text="new", start=1452.0, end=1452.3),
        Word(text="game", start=1452.4, end=1452.8),
    ]
    # One dull frame (1470 s) is a flicker; two in a row (1492 s) are a death screen.
    screens = [("gameplay", 0.2)] * 10 + [("menu", 0.7)] + [("gameplay", 0.2)] * 10
    screens += [("menu", 0.7)] * 2 + [("gameplay", 0.2)] * 9
    rows = build_rows(1450.0, 1514.0, screens, events, text, words)
    assert rows[0].t == 1450.0 and rows[1].speech == "new game"
    ends = scene_ends(rows)
    assert [t for t, _ in ends] == [1452.0, 1492.0, 1500.0]
    assert "new game" in ends[0][1] and "menu" in ends[1][1] and "KILLED BY" in ends[2][1]


def _analysis(start: float = 1470.0, end: float = 1493.0) -> ClipAnalysis:
    rows = [Row(t=float(t)) for t in range(1450, 1514, 2)]
    for row in rows:
        if row.t in (1496.0, 1498.0):
            row.sounds = ["gunfire"]
        if row.t == 1500.0:
            row.text = ["KILLED BY HUSH"]
    return ClipAnalysis(
        id=0, start=start, end=end, zone_start=1450.0, zone_end=1513.0, rows=rows,
        scene_ends=[(1500.0, '"KILLED BY HUSH" on screen')],
        actions=[1496.0, 1498.0, 1500.0],
    )  # fmt: skip


def test_summary_and_timeline_say_what_is_outside_the_clip() -> None:
    analysis = _analysis()
    assert analysis.summary() == [
        "No action (fight sounds or kill text) inside the clip.",
        "Action continues 3 s after the clip ends.",
    ]
    text = describe(analysis)
    assert "Clip 0: now 24:30-24:53 (23 s); analysed 24:10-25:13" in text
    assert text.index("CLIP END") < text.index("[24:56] gunfire")
    assert '[25:00] text "KILLED BY HUSH" | SCENE ENDS' in text


def test_clip_never_crosses_a_scene_end() -> None:
    analysis = _analysis(1480.0, 1512.0)
    # More action before the end at 25:00: the clip ends there.
    assert cut_at_scene_end(1480.0, 1512.0, analysis) == (1480.0, 1500.0)
    quiet_before = ClipAnalysis(**{**analysis.__dict__, "actions": [1505.0, 1508.0]})
    assert cut_at_scene_end(1480.0, 1512.0, quiet_before) == (1500.0, 1512.0)


def test_ocr_times_are_thinned() -> None:
    assert len(ocr_times(0.0, 200.0)) == 12
    assert ocr_times(10.0, 22.0) == [10.0, 14.0, 18.0]


def test_analyse_uses_the_readers() -> None:
    seen: dict[str, Any] = {}

    def screens(zone_start: float, length: float) -> list[tuple[str, float]]:
        seen["zone"] = (zone_start, length)
        return [("gameplay", 0.1)] * 40

    readers = VideoReaders(
        screens=screens,
        text=lambda times: ScreenText(frames={f"{times[0]:g}": ["VICTORY"]}),
        frame=lambda t: b"jpeg" if t < 100 else None,
    )
    analysis = analyse(3, 50.0, 70.0, 95.0, events=None, words=[], readers=readers)
    assert seen["zone"] == (30.0, 60.0)
    assert [label for label, _ in analysis.frames] == ["start", "1/3", "2/3", "end", "after end"]
    assert analysis.scene_ends[0][1] == '"VICTORY" on screen'
    assert analysis.actions == [30.0]


def test_images_follow_the_text_as_data_urls() -> None:
    assert user_content("hi", []) == "hi"
    parts = user_content("hi", [b"\xff\xd8"])
    assert isinstance(parts, list)
    assert parts[0] == {"type": "text", "text": "hi"}
    assert parts[1]["image_url"]["url"] == "data:image/jpeg;base64,/9g="


def draft(start: float, title: str = "Draft") -> SelectionClip:
    return SelectionClip(
        start=start, end=start + 30, title=title, youtube_title=title,
        youtube_description="d", hashtags=["#a", "#b", "#c"], linkedin_post="p",
        reason="r", score=0.5,
    )  # fmt: skip


def reviewer(clip_id: int, start: float, end: float) -> ClipAnalysis:
    scene = [(start + 15, 'said "new game"')] if clip_id == 1 else []
    return ClipAnalysis(
        id=clip_id, start=start, end=end, zone_start=start - 20, zone_end=end + 20,
        rows=[], scene_ends=scene, actions=[start + 5, start + 8],
        frames=[("start", b"jpeg"), ("end", b"jpeg")],
    )  # fmt: skip


class ReviewLlm:
    def __init__(self, verdicts: list[ReviewClip], images_fail: bool = False) -> None:
        self.verdicts = verdicts
        self.images_fail = images_fail
        self.calls: list[tuple[str, int]] = []

    def complete_json(
        self, system: str, user: str, schema: object, temperature: float, images: Any = ()
    ) -> object:
        if schema is TitleAnswer:
            return TitleAnswer(clips=[TitleText(id=0, title="Fallback text")])
        assert schema is ReviewAnswer
        self.calls.append((user, len(images)))
        if images and self.images_fail:
            raise AppError(ErrorCode.LLM_BAD_RESPONSE, "no image input")
        return ReviewAnswer(clips=self.verdicts)


def test_review_keeps_fixes_and_drops() -> None:
    drafts = [draft(100), draft(300), draft(500), draft(700)]
    verdicts = [
        ReviewClip(id=0, keep=False, problem="nothing happens"),
        ReviewClip(id=2, start=490.0, end=525.0, title="Look at that clutch", score=0.9),
        ReviewClip(id=1, start=300.0, end=330.0, title="New game chaos", score=0.6),
    ]
    llm = ReviewLlm(verdicts)
    kept = review_clips(drafts, 3, FACTS, None, llm, reviewer)  # type: ignore[arg-type]
    assert [c.title for c in kept] == ["Look at that clutch", "New game chaos", "Draft"]
    assert (kept[0].start, kept[0].end) == (490.0, 525.0)
    # Draft 1 crossed "new game" at 315 s with more action before it: cut there.
    assert (kept[1].start, kept[1].end) == (300.0, 315.0) or kept[1].end <= 315.0
    assert kept[2].start == 700.0  # not mentioned: fills the last place as it was
    assert all(c.start != 100.0 for c in kept)  # dropped
    assert llm.calls[0][1] == 8 and "Images: start, end" in llm.calls[0][0]


def test_review_retries_without_images_then_falls_back() -> None:
    llm = ReviewLlm([ReviewClip(id=0, title="Fine")], images_fail=True)
    kept = review_clips([draft(100)], 1, FACTS, None, llm, reviewer)  # type: ignore[arg-type]
    assert [n for _, n in llm.calls] == [2, 0]
    assert "Images:" not in llm.calls[1][0]
    assert kept[0].title == "Fine"

    class Down:
        def complete_json(self, *_a: Any, **_k: Any) -> object:
            raise AppError(ErrorCode.LLM_RATE_LIMITED, "slow down")

    drafts = [draft(100), draft(300)]
    assert review_clips(drafts, 1, FACTS, None, Down(), reviewer) == drafts[:1]  # type: ignore[arg-type]


def test_round_one_drafts_twice_as_many(tmp_path: Path) -> None:
    assert draft_count(5, None) == 5
    assert draft_count(5, reviewer) == 10
    assert draft_count(3, reviewer) == 6
    words = [Word(text=f"w{i}", start=float(i), end=float(i) + 0.8) for i in range(1500)]
    transcript = TranscriptFile(
        language="en", spans=[(0.0, 1500.0)],
        segments=[Segment(start=0.0, end=1500.0, text="words", words=words)],
    )  # fmt: skip
    candidates = [
        Candidate(peak_t=t + 25, start=t, end=t + 60, score=0.5, signals=SignalScores(audio=0.5))
        for t in (100.0, 300.0, 500.0)
    ]
    systems: list[str] = []

    class Llm:
        def complete_json(
            self, system: str, user: str, schema: object, *_a: Any, **_k: Any
        ) -> object:
            if schema is SelectionAnswer:
                systems.append(system)
                return SelectionAnswer(clips=[])
            if schema is ReviewAnswer:
                return ReviewAnswer(clips=[])
            return TitleAnswer(clips=[])

    result = run_select(
        tmp_path, FACTS, candidates, transcript, count=2, extended=False,
        client=Llm(), reviewer=reviewer,  # type: ignore[arg-type]
    )  # fmt: skip
    assert "up to 4 moments" in systems[0]
    assert len(result.clips) == 2


@pytest.mark.parametrize("value", ["kda", "LOADOUT"])
def test_the_in_round_scoreboard_is_not_a_scene_end(value: str) -> None:
    # TAB shows "KDA" and "LOADOUT" mid-round (owner's video, 10:05 and 20:54).
    rows = [Row(t=0.0), Row(t=2.0, text=[value])]
    assert scene_ends(rows) == []


@pytest.mark.parametrize("value", ["ROUND22", "MATCHPOINT", "PRESSTO BUY", "KILLEDBY HUSH"])
def test_round_words_without_spaces_end_a_scene(value: str) -> None:
    # Real OCR readings from the owner's video, spaces dropped.
    rows = [Row(t=0.0), Row(t=2.0, text=[value])]
    assert scene_ends(rows)[0][0] == 2.0
