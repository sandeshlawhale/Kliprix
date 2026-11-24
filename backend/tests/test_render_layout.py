"""Layout math, ASS generation (golden files), caption chunking, and face tracking."""

import os
from pathlib import Path

import pytest

from app.pipeline.face_track import (
    Face,
    Sample,
    center_at,
    choose_targets,
    face_ratio,
    fill_gaps,
    smooth_path,
)
from app.pipeline.transcribe import Word
from app.render.ass import ass_color, ass_time, build_ass, chunk_words, clean, fit_title
from app.render.fonts import text_width
from app.render.layout import blur_radius, compute_layout, crop_left, face_crop_box

GOLDEN = Path(__file__).parent / "golden"
# Set KLIPRIX_UPDATE_GOLDEN=1 to rewrite the golden files after an intended change.
UPDATE = os.environ.get("KLIPRIX_UPDATE_GOLDEN") == "1"


def test_face_layout_matches_spec() -> None:
    layout = compute_layout("face", 1920, 1080)
    assert (layout.fg_w, layout.fg_h, layout.fg_top, layout.fg_bottom) == (1080, 1350, 240, 1590)
    assert layout.title_y == 150  # max(150, 240 - 110)
    assert layout.caption_y == 1344  # min(1344, 1590 + 130): 30% above the bottom (D-052)


def test_full_frame_layout_for_16_9() -> None:
    layout = compute_layout("full", 1920, 1080)
    assert (layout.fg_w, layout.fg_h) == (1080, 608)
    assert layout.fg_top == 900 - 304
    assert layout.title_y == 596 - 110
    assert layout.caption_y == 596 + 608 + 130
    # Captions stay above the bottom safe area (bottom 160 px).
    assert layout.caption_y < 1920 - 160


def test_face_crop_box_and_clamping() -> None:
    box = face_crop_box(1920, 1080)
    assert (box.w, box.h) == (864, 1080)
    assert crop_left(0.5, 1920, box) == 528
    assert crop_left(0.0, 1920, box) == 0
    assert crop_left(1.0, 1920, box) == 1920 - 864
    narrow = face_crop_box(720, 1280)  # vertical source: full width, 4:5 height
    assert (narrow.w, narrow.h) == (720, 900)


def test_blur_mapping() -> None:
    assert blur_radius(0) == 0
    assert blur_radius(30) == 8
    assert blur_radius(60) == 15
    assert blur_radius(99) == 15


def test_ass_helpers() -> None:
    assert ass_color("#FFD60A") == "&H000AD6FF"
    assert ass_color("#000000", 0.6) == "&H66000000"
    assert ass_time(3723.456) == "1:02:03.46"
    assert clean("a{\\b1}b\nc") == "a(/b1)b c"


def test_text_width_uses_font_metrics() -> None:
    narrow = text_width("IIII", "Anton-Regular.ttf", 88)
    wide = text_width("WWWW", "Anton-Regular.ttf", 88)
    assert 0 < narrow < wide
    assert text_width("ab", "Montserrat-ExtraBold.ttf", 144) == pytest.approx(
        2 * text_width("ab", "Montserrat-ExtraBold.ttf", 72)
    )


def test_fit_title_wraps_and_shrinks() -> None:
    size, lines = fit_title("short")
    assert (size, lines) == (88, ["SHORT"])
    size, lines = fit_title("this title is long enough for two lines")
    assert len(lines) <= 2
    assert all(text_width(line, "Anton-Regular.ttf", size) <= 960 for line in lines)
    size, lines = fit_title("WWWWWWWWWWWWWWW WWWWWWWWWWWWWWW WWWWWWWWWWWWWWW")
    assert size < 88


def words(spec: list[tuple[str, float, float]]) -> list[Word]:
    return [Word(text=t, start=s, end=e) for t, s, e in spec]


SAMPLE = words(
    [
        ("No", 0.0, 0.3),
        ("way,", 0.3, 0.6),
        ("he", 0.65, 0.8),
        ("actually", 0.8, 1.2),
        ("hit", 1.2, 1.4),
        ("that", 1.4, 1.7),
        ("shot!", 2.5, 3.0),
        ("क्या", 3.0, 3.4),
    ]
)


def test_chunking_rules() -> None:
    chunks = chunk_words(SAMPLE, max_words=4)
    texts = [[w.text for w in chunk.words] for chunk in chunks]
    # Break after "way," (punctuation), after "that" (0.8 s pause), after "shot!".
    assert texts == [
        ["No", "way,"],
        ["he", "actually", "hit", "that"],
        ["shot!"],
        [SAMPLE[-1].text],
    ]
    for chunk in chunks:
        assert len(chunk.lines) <= 2
        for line in chunk.lines:
            assert len(" ".join(chunk.words[i].text for i in line)) <= 18
    # A chunk ends at the next chunk's start or linger after its last word.
    assert chunks[0].end == pytest.approx(0.65)
    assert chunks[1].end == pytest.approx(2.0)
    pop = chunk_words(SAMPLE, max_words=3)
    assert all(len(chunk.words) <= 3 for chunk in pop)


@pytest.mark.parametrize("style", ["pop", "karaoke", "boxed"])
def test_ass_golden(style: str) -> None:
    layout = compute_layout("face", 1920, 1080)
    text = build_ass(SAMPLE, style, "He actually hit that shot", layout, 4.0)  # type: ignore[arg-type]
    golden = GOLDEN / f"captions_{style}.ass"
    if UPDATE:
        golden.parent.mkdir(exist_ok=True)
        golden.write_text(text, encoding="utf-8")
    assert text == golden.read_text(encoding="utf-8")


def test_ass_has_one_event_per_word_slice() -> None:
    layout = compute_layout("full", 1920, 1080)
    text = build_ass(SAMPLE, "karaoke", None, layout, 4.0)
    dialogue = [line for line in text.splitlines() if line.startswith("Dialogue:")]
    assert len(dialogue) == len(SAMPLE)
    assert "Title" not in "".join(dialogue)
    assert "\\k" not in text
    assert "Noto Sans Devanagari" in text


# ---------- face tracking ----------


def sample(*faces: tuple[float, float], cut: bool = False) -> Sample:
    return Sample(faces=[Face(cx, area) for cx, area in faces], scene_cut=cut)


def test_face_ratio() -> None:
    assert face_ratio([sample((0.5, 0.1)), sample(), sample(), sample((0.2, 0.1))]) == 0.5
    assert face_ratio([]) == 0.0


def test_target_switch_needs_bigger_face_for_one_second() -> None:
    dt = 0.1
    small_then_big = [sample((0.2, 0.10))] + [sample((0.2, 0.10), (0.8, 0.20))] * 15
    targets = choose_targets(small_then_big, dt)
    assert targets[5] == 0.2  # challenger not held long enough yet
    assert targets[-1] == 0.8  # switched after 1 s
    not_big_enough = [sample((0.2, 0.10))] + [sample((0.2, 0.10), (0.8, 0.13))] * 15
    assert choose_targets(not_big_enough, dt)[-1] == 0.2


def test_fill_gaps_holds_then_recenters() -> None:
    dt = 0.5
    filled = fill_gaps([0.9] + [None] * 12, dt)
    assert filled[6] == 0.9  # 3 s hold
    assert filled[-1] == pytest.approx(0.5)  # back at the center 2 s later
    assert fill_gaps([None, None, 0.3], dt)[0] == 0.3


def test_smoothing_dead_zone_speed_and_cuts() -> None:
    dt = 0.1
    steady = smooth_path([0.5, 0.51, 0.52, 0.5], [False] * 4, dt)
    assert steady == [0.5] * 4  # inside the 3% dead zone
    moving = smooth_path([0.2] + [0.8] * 3, [False] * 4, dt)
    assert moving[1] - moving[0] <= 0.06 + 1e-9  # max pan 0.6 width per second
    cut = smooth_path([0.2, 0.8], [False, True], dt)
    assert cut[1] == 0.8


def test_center_at_interpolates() -> None:
    path = [0.2, 0.5]
    assert center_at(path, 0, 3) == 0.2
    assert center_at(path, 3, 3) == 0.5
    assert center_at(path, 1, 3) == pytest.approx(0.3)
    assert center_at(path, 99, 3) == 0.5
    assert center_at([], 4, 3) == 0.5
