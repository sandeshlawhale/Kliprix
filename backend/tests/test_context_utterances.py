"""Utterance segmentation (D-050): sentences, pauses, missing punctuation, speakers, long
utterances and invalid timestamps."""

import math

import pytest

from app.pipeline.context.utterances import clean_words, segment
from app.pipeline.transcribe import Word
from tests.context_fixtures import say, speech


def texts(words: list[Word], **kwargs: object) -> list[str]:
    return [u.text for u in segment(words, **kwargs).utterances]  # type: ignore[arg-type]


def test_complete_sentences_are_utterances() -> None:
    words = say(10.0, "Hello there. How are you? I am fine!")
    assert texts(words) == ["Hello there.", "How are you?", "I am fine!"]
    index = segment(words)
    assert [u.id for u in index.utterances] == ["U0", "U1", "U2"]
    assert index.utterances[0].start == 10.0
    assert index.utterances[1].start == words[2].start


def test_pauses_split_without_punctuation() -> None:
    words = speech((10.0, "we push now"), (12.0, "they are on site"))
    # 0.9 s pause after "now" (not a connector): two utterances.
    assert texts(words) == ["we push now", "they are on site"]
    # Short gaps between words never split.
    assert texts(say(10.0, "we push now they are on site")) == ["we push now they are on site"]


def test_a_connector_holds_the_utterance_over_a_short_pause() -> None:
    held = speech((10.0, "I went to the"), (12.3, "store yesterday"))
    assert texts(held) == ["I went to the store yesterday"]
    # A long pause ends it anyway.
    split = speech((10.0, "I went to the"), (13.0, "store yesterday"))
    assert texts(split) == ["I went to the", "store yesterday"]


def test_speaker_changes_split() -> None:
    words = say(10.0, "you go first no you go")
    speakers = ["A", "A", "A", "B", "B", "B"]
    assert texts(words, speakers=speakers) == ["you go first", "no you go"]
    with pytest.raises(ValueError, match="one label per word"):
        segment(words, speakers=["A"])


def test_long_utterances_split_at_a_comma_or_the_longest_pause() -> None:
    first = " ".join(f"a{i}" for i in range(24)) + ","
    second = " ".join(f"b{i}" for i in range(24))
    words = say(0.0, f"{first} {second}")
    index = segment(words, max_s=15.0)
    pieces = index.utterances
    assert len(pieces) == 2
    assert pieces[0].text.endswith("a23,")
    assert pieces[0].continues and not pieces[0].continued
    assert pieces[1].continued and not pieces[1].continues
    assert all(p.length <= 15.0 for p in pieces)
    # Without a comma it splits at the longest pause.
    run_on = segment(say(0.0, " ".join(f"w{i}" for i in range(40))), max_s=15.0).utterances
    assert len(run_on) == 2 and all(p.length <= 15.0 for p in run_on)


def test_invalid_timestamps_are_dropped() -> None:
    words = [
        Word(text="ok", start=1.0, end=1.3),
        Word.model_construct(text="nan", start=math.nan, end=2.0),  # bypasses validation
        Word(text="backwards", start=3.0, end=2.5),
        Word.model_construct(text="negative", start=-1.0, end=0.5),
        Word(text="  ", start=4.0, end=4.2),
        Word.model_construct(text="inf", start=5.0, end=math.inf),
        Word(text="fine", start=6.0, end=6.3),
        Word(text="dup", start=6.1, end=6.2),  # inside "fine"
        Word(text="overlap", start=6.2, end=6.6),  # starts before "fine" ends
    ]
    cleaned = clean_words(words)
    assert [w.text for w in cleaned] == ["ok", "fine", "overlap"]
    assert cleaned[2].start == 6.3
    assert segment([]).utterances == []


def test_lookups() -> None:
    index = segment(speech((10.0, "first sentence here."), (20.0, "second one.")))
    assert index.at(10.5) is index.utterances[0]
    assert index.at(10.0) is None  # on the edge
    assert index.at(15.0) is None  # in the gap
    assert index.get("u1") is index.utterances[1]
    assert index.word_at(10.1) is not None and index.word_at(10.35) is None
    assert index.previous(index.utterances[1]) is index.utterances[0]
    assert index.following(index.utterances[1]) is None
    assert index.speech_edges(0, 100) == (10.0, index.utterances[1].end)
