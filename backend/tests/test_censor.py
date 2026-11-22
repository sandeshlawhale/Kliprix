"""Bad-word censoring in rendered clips (D-048)."""

from __future__ import annotations

from pathlib import Path

from app.pipeline.transcribe import Word
from app.render.censor import (
    already_starred,
    bleep_spans,
    censor_text,
    censor_words,
    is_bad,
    mask,
)
from app.render.compose import RenderJob, write_ass
from app.render.layout import compute_layout


def test_mask_hides_the_middle_half() -> None:
    assert mask("fuck") == "f**k"
    assert mask("Fuck!") == "F**k!"
    assert mask("chutiya") == "ch****a"
    assert mask("bc") == "b*"
    assert mask("bkl,") == "b**,"
    assert mask("madarchod") == "mad****od"


def test_is_bad_ignores_case_and_punctuation() -> None:
    assert is_bad("FUCKING!")
    assert is_bad("Behenchod,")
    assert is_bad("bsdk")
    assert is_bad("चूतिया")
    for clean in ("bhai", "chod", "saala", "duck", "class", "bachcha", "lagai", "kutta", ""):
        assert not is_bad(clean), clean


def test_already_starred() -> None:
    assert already_starred("F***")
    assert already_starred("Bh**")
    assert not already_starred("***")
    assert not already_starred("hello")


def test_censor_text_and_words_keep_the_rest() -> None:
    assert censor_text("What the fuck bhai") == "What the f**k bhai"
    words = [Word(text="kya", start=0, end=0.3), Word(text="chutiya", start=0.3, end=0.8)]
    out = censor_words(words)
    assert [w.text for w in out] == ["kya", "ch****a"]
    assert out[1].start == 0.3 and out[1].end == 0.8
    assert words[1].text == "chutiya"  # the stored words are never changed


def test_bleep_spans_merge_and_clamp() -> None:
    words = [
        Word(text="okay", start=0.0, end=0.4),
        Word(text="fuck", start=1.0, end=1.3),
        Word(text="this", start=1.32, end=1.5),
        Word(text="shit", start=1.36, end=1.6),
        Word(text="F***", start=5.0, end=5.4),
        Word(text="bc", start=9.9, end=10.2),
    ]
    assert bleep_spans(words, 10.0) == [(0.95, 1.65), (4.95, 5.45), (9.85, 10.0)]
    assert bleep_spans([Word(text="hello", start=0, end=1)], 5.0) == []


def test_write_ass_censors_captions_and_title(tmp_path: Path) -> None:
    job = RenderJob(
        work_dir=tmp_path,
        fg_file="cut.mp4",
        offset_s=0,
        duration_s=3,
        layout=compute_layout("full", 1920, 1080),
        blur=20,
        style="pop",
        words=[Word(text="what", start=0, end=0.5), Word(text="fuck", start=0.5, end=1)],
        title="Fucking crazy",
    )
    text = write_ass(job).read_text(encoding="utf-8")
    assert "f**k" in text
    assert "FU****G CRAZY" in text
    assert "fuck" not in text.lower()
