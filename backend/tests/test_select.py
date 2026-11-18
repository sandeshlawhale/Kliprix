"""Select stage: validation and repair of LLM clips, fallback fill, and selection.json."""

import itertools
from pathlib import Path

from app.llm.schemas import LlmClip, SelectionAnswer
from app.pipeline.candidates import Candidate, SignalScores
from app.pipeline.select import (
    SELECTION_FILE_NAME,
    VideoFacts,
    build_selection,
    clean_hashtags,
    clean_title,
    fit_window,
    load_cached,
    run_select,
    truncate_words,
)
from app.pipeline.transcribe import Segment, TranscriptFile, Word

FACTS = VideoFacts(title="My Stream", channel="Chan", duration_s=1000.0)


def cand(start: float, score: float = 0.5) -> Candidate:
    return Candidate(
        peak_t=start + 25,
        start=start,
        end=start + 60,
        score=score,
        signals=SignalScores(audio=0.5, motion=0.25),
    )


CANDIDATES = [cand(100, 0.9), cand(300, 0.8), cand(500, 0.7), cand(700, 0.6), cand(900, 0.5)]


def transcript() -> TranscriptFile:
    words = [Word(text=f"w{i}", start=float(i), end=float(i) + 0.8) for i in range(0, 1000, 1)]
    segments = [
        Segment(start=float(s), end=float(s + 10), text="line", words=words[s : s + 10])
        for s in range(0, 1000, 10)
    ]
    return TranscriptFile(language="en", spans=[(0.0, 1000.0)], segments=segments)


def llm(**fields: object) -> LlmClip:
    base: dict[str, object] = {
        "candidate_id": 0,
        "start": 110.0,
        "end": 150.0,
        "title": "Nice play",
        "youtube_title": "Nice play #shorts",
        "youtube_description": "desc",
        "hashtags": ["#gaming", "#fun", "#shorts"],
        "linkedin_post": "post",
        "reason": "why",
        "score": 0.9,
    }
    base.update(fields)
    return LlmClip.model_validate(base)


def test_valid_clip_is_kept_with_snapped_times() -> None:
    answer = SelectionAnswer(clips=[llm(start=110.3, end=150.5)])
    result = build_selection(answer, CANDIDATES, FACTS, transcript(), 1, extended=False)
    clip = result.clips[0]
    # 110.3 is inside word w110 (110.0-110.8): start moves back; 150.5 -> end of w150 (150.8).
    assert (clip.start, clip.end) == (110.0, 150.8)
    assert clip.title == "Nice play"


def test_too_long_clip_is_trimmed_to_sixty_in_standard_mode() -> None:
    answer = SelectionAnswer(clips=[llm(start=100.0, end=185.0, needs_extended=True)])
    clip = build_selection(answer, CANDIDATES, FACTS, None, 1, extended=False).clips[0]
    assert clip.end - clip.start == 60.0
    assert not clip.needs_extended


def test_extended_clip_allowed_up_to_ninety() -> None:
    answer = SelectionAnswer(clips=[llm(start=100.0, end=180.0, needs_extended=True)])
    clip = build_selection(answer, CANDIDATES, FACTS, None, 1, extended=True).clips[0]
    assert clip.end - clip.start == 80.0
    assert clip.needs_extended


def test_short_clip_is_extended_to_thirty() -> None:
    answer = SelectionAnswer(clips=[llm(start=120.0, end=130.0)])
    clip = build_selection(answer, CANDIDATES, FACTS, None, 1, extended=False).clips[0]
    assert clip.end - clip.start == 30.0


def test_times_clamped_to_candidate_region_and_video() -> None:
    answer = SelectionAnswer(clips=[llm(candidate_id=4, start=880.0, end=1100.0)])
    clip = build_selection(answer, CANDIDATES, FACTS, None, 1, extended=False).clips[0]
    assert clip.end <= 1000.0
    assert clip.start >= 870.0


def test_overlapping_and_close_clips_are_dropped_and_filled() -> None:
    answer = SelectionAnswer(
        clips=[
            llm(start=100.0, end=150.0, score=0.9),
            llm(start=140.0, end=190.0, score=0.8),  # overlaps
            llm(candidate_id=1, start=170.0, end=210.0, score=0.7),  # gap < 30 s
        ]
    )
    result = build_selection(answer, CANDIDATES, FACTS, None, 3, extended=False)
    assert len(result.clips) == 3
    starts = sorted(clip.start for clip in result.clips)
    for first, second in itertools.pairwise(starts):
        assert second - first >= 30
    fallback = [clip for clip in result.clips if clip.reason.startswith("Picked from")]
    assert len(fallback) == 2


def test_fallback_when_llm_returns_nothing() -> None:
    result = build_selection(SelectionAnswer(clips=[]), CANDIDATES, FACTS, transcript(), 5, False)
    assert len(result.clips) == 5
    first = result.clips[0]
    assert first.title.startswith("w100")
    assert len(first.hashtags) == 3
    assert 25 <= first.end - first.start <= 60


def test_fallback_title_without_speech() -> None:
    result = build_selection(SelectionAnswer(clips=[]), CANDIDATES, FACTS, None, 1, False)
    assert result.clips[0].title == "Highlight at 01:40"
    assert "My Stream" in result.clips[0].youtube_description


def test_never_more_than_requested() -> None:
    answer = SelectionAnswer(clips=[llm(candidate_id=i, start=c.start, end=c.end) for i, c in
                                    enumerate(CANDIDATES)])  # fmt: skip
    assert len(build_selection(answer, CANDIDATES, FACTS, None, 2, False).clips) == 2


def test_text_repairs() -> None:
    assert clean_title('"An Absolutely Insane Clutch Play In The Final Round 😂"') == (
        "An Absolutely Insane Clutch Play In The"
    )
    assert len(clean_title("x" * 80)) == 40
    assert clean_hashtags(["gaming", "#Funny Clips", "#", "#gaming", "#a b"]) == [
        "#gaming",
        "#FunnyClips",
        "#ab",
    ]
    assert clean_hashtags(["#one"]) == ["#one", "#shorts", "#highlights"]
    assert len(clean_hashtags([f"#t{i}" for i in range(10)])) == 6
    assert truncate_words("hello wonderful world", 12) == "hello"


def test_fit_window_rejects_impossible() -> None:
    assert fit_window(990.0, 980.0, 1000.0, 60.0, []) is None
    assert fit_window(0.0, 10.0, 20.0, 60.0, []) is None


class FakeClient:
    def __init__(self, answer: SelectionAnswer) -> None:
        self.answer = answer
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: object, temperature: float) -> object:
        self.calls += 1
        assert "Candidate 0" in user
        assert temperature == 0.3
        return self.answer


def test_run_select_writes_and_caches(tmp_path: Path) -> None:
    fake = FakeClient(SelectionAnswer(clips=[llm()]))
    result = run_select(
        tmp_path, FACTS, CANDIDATES, transcript(), count=2, extended=False,
        client=fake,  # type: ignore[arg-type]
    )  # fmt: skip
    assert (tmp_path / SELECTION_FILE_NAME).is_file()
    assert load_cached(tmp_path) == result
    again = run_select(
        tmp_path, FACTS, CANDIDATES, transcript(), count=2, extended=False,
        client=fake,  # type: ignore[arg-type]
    )  # fmt: skip
    assert again == result
    assert fake.calls == 1
