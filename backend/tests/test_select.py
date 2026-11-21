"""Select stage: validation and repair of LLM clips, fallback fill, and selection.json."""

import itertools
from pathlib import Path

from app.config import SPEECH_FLOOR
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
    rank_by_speech,
    run_select,
    select_more,
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
    # No sentence edge near (no punctuation, 0.2 s gaps), so word edges: 110.3 is inside w110
    # (110.0-110.8) and 150.5 inside w150 (-150.8). Then 0.15 s / 0.25 s of room, but never
    # into w109 (ends 109.8) or w151 (starts 151.0).
    assert (clip.start, clip.end) == (109.85, 151.0)
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


def test_short_clip_is_extended_to_fifteen() -> None:
    # D-035: 15 s is enough for a quick moment; shorter ones grow to 15 s.
    answer = SelectionAnswer(clips=[llm(start=120.0, end=130.0)])
    clip = build_selection(answer, CANDIDATES, FACTS, None, 1, extended=False).clips[0]
    assert clip.end - clip.start == 15.0


def test_a_twenty_second_moment_stays_twenty_seconds() -> None:
    answer = SelectionAnswer(clips=[llm(start=120.0, end=140.0)])
    clip = build_selection(answer, CANDIDATES, FACTS, None, 1, extended=False).clips[0]
    assert clip.end - clip.start == 20.0


def test_most_important_clip_comes_first_with_its_category() -> None:
    answer = SelectionAnswer(
        clips=[
            llm(candidate_id=0, start=100.0, end=130.0, score=0.5),
            llm(candidate_id=4, start=880.0, end=910.0, score=0.9).model_copy(
                update={"category": "funny", "reason": "He falls off the chair."}
            ),
        ]
    )
    clips = build_selection(answer, CANDIDATES, FACTS, None, 2, extended=True).clips
    assert clips[0].start >= 870.0
    assert clips[0].reason == "Funny: He falls off the chair."


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
    assert fit_window(0.0, 5.0, 10.0, 60.0, []) is None


def sentences(starts: list[float], words_each: int = 8) -> list[Word]:
    """Sentences of `words_each` words, 0.5 s apart, ending with a full stop at each start in
    `starts`, with no pause between sentences (only the punctuation ends them)."""
    words: list[Word] = []
    for first in starts:
        for i in range(words_each):
            text = f"s{int(first)}w{i}" + ("." if i == words_each - 1 else "")
            words.append(Word(text=text, start=first + i * 0.5, end=first + i * 0.5 + 0.4))
    return words


def test_fit_window_snaps_to_whole_sentences() -> None:
    # Sentences start every 4 s: 100, 104, ... each ends 3.9 s later.
    words = sentences([100.0 + 4 * k for k in range(30)])
    # Starts mid-sentence (106.2) and ends mid-sentence (141.3).
    window = fit_window(106.2, 141.3, 1000.0, 60.0, words)
    assert window is not None
    start, end = window
    # Nearest sentence start is 108.0 (not 104.0); nearest end is 139.9 (not 143.9).
    assert start == 108.0 - 0.1  # room up to the previous word's end (107.9)
    assert end == 139.9 + 0.1  # room up to the next sentence's first word (140.0)


def test_fit_window_trims_long_clips_at_a_sentence_end() -> None:
    words = sentences([100.0 + 4 * k for k in range(40)])
    window = fit_window(100.0, 185.0, 1000.0, 60.0, words)
    assert window is not None
    start, end = window
    # The last sentence end that fits in 60 s from 100.0 is 159.9 (sentence at 156); room
    # around it would pass 60 s, so none is added.
    assert (start, end) == (100.0, 159.9)


def test_fit_window_grows_short_clips_to_a_sentence_end() -> None:
    words = sentences([100.0 + 4 * k for k in range(40)])
    window = fit_window(100.0, 108.0, 1000.0, 60.0, words)
    assert window is not None
    # The first sentence end at least 15 s after 100.0 is 115.9 (sentence at 112).
    assert window[1] == 115.9 + 0.1


def test_rank_by_speech_demotes_silent_windows() -> None:
    talk = cand(100, 0.6)
    silent = cand(300, 0.9)  # the intro screen: top signals, nobody talking
    words = [Word(text="hi", start=100.0 + i * 0.5, end=100.0 + i * 0.5 + 0.3) for i in range(120)]
    ranked = rank_by_speech([silent, talk], words + sentences([500.0], 60))
    assert [c.start for c in ranked] == [100.0, 300.0]
    assert ranked[0].signals.speech == 1.0
    assert ranked[1].signals.speech == 0.0
    assert ranked[1].score == round(0.9 * SPEECH_FLOOR, 3)


def test_rank_by_speech_keeps_videos_without_speech() -> None:
    few = [Word(text="oh", start=110.0, end=110.3)]
    ranked = rank_by_speech([cand(300, 0.9), cand(100, 0.6)], few)
    assert [(c.start, c.score, c.signals.speech) for c in ranked] == [
        (300.0, 0.9, None),
        (100.0, 0.6, None),
    ]


def test_clips_already_made_block_new_ones() -> None:
    # A clip exists at 300-360: candidate 1 (300-360) and anything within 30 s are refused.
    answer = SelectionAnswer(clips=[llm(candidate_id=1, start=310.0, end=350.0)])
    result = build_selection(answer, CANDIDATES, FACTS, None, 2, False, taken=[(300.0, 360.0)])
    assert [clip.start for clip in result.clips] == [100.0, 500.0]
    assert all(clip.reason.startswith("Picked from") for clip in result.clips)


def test_select_more_lists_clips_already_made() -> None:
    fake = FakeClient(SelectionAnswer(clips=[]))
    clips = select_more(
        FACTS, CANDIDATES, None, [(300.0, 360.0)], count=1, extended=False,
        client=fake,  # type: ignore[arg-type]
    )  # fmt: skip
    assert fake.calls == 1
    assert "Clips already made (do not repeat these moments): 05:00-06:00" in fake.last_user
    assert [clip.start for clip in clips] == [100.0]
    assert select_more(FACTS, [], None, [], count=1, extended=False, client=fake) == []  # type: ignore[arg-type]
    assert fake.calls == 1


def test_fallback_skips_windows_without_speech() -> None:
    silent = cand(300, 0.9).model_copy(
        update={"signals": SignalScores(chat=0.9, audio=0.4, motion=0.0, speech=0.0)}
    )
    result = build_selection(
        SelectionAnswer(clips=[]), [silent, cand(100, 0.5)], FACTS, transcript(), 2, False
    )
    assert [clip.start for clip in result.clips] == [100.0]


class FakeClient:
    def __init__(self, answer: SelectionAnswer) -> None:
        self.answer = answer
        self.calls = 0
        self.last_user = ""

    def complete_json(self, system: str, user: str, schema: object, temperature: float) -> object:
        self.calls += 1
        self.last_user = user
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
