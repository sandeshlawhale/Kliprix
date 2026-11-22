"""On-screen text (D-045): which frames are read, overlays dropped, caching, and frames grabbed
from a real (synthetic) video with a fake reader."""

import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.pipeline.candidates import Candidate, SignalScores
from app.pipeline.screen_text import (
    ScreenText,
    frame_times,
    keep_text,
    load_text,
    overlay_texts,
    read_frames,
    texts_between,
)

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")


def cand(peak: float, cause: float | None = None) -> Candidate:
    return Candidate(
        peak_t=peak, start=max(0.0, peak - 25), end=peak + 35, score=0.5,
        signals=SignalScores(audio=0.5), cause_t=cause,
    )  # fmt: skip


def test_frame_times_around_the_peak_or_the_cause() -> None:
    times = frame_times([cand(100), cand(300, cause=280), cand(5)], 400.0)
    # -5 s for the peak at 5 s is before the video: dropped.
    assert times == [5.0, 15.0, 90.0, 100.0, 110.0, 270.0, 280.0, 290.0]


def test_overlays_and_short_noise_are_dropped() -> None:
    # The same overlay read four ways (real readings from the owner's stream).
    # Other texts are on 2 of 6 frames at most, below the 50% overlay share.
    frames = {
        "10": ["FOLLOW GAMERFLEETON KICK.", "KILLED BY REYNA"],
        "20": ["FOLLOWGAMERFLEET", "ONKICK.", "BUY PHASE"],
        "30": ["FOLLOW GAMERFLEET ON KICK.", "ACE", "Buy Phase"],
        "40": ["FOLLOWGAMERFLEETONKICK."],
        "50": ["VICTORY"],
        "60": ["ROUND ONGOING"],
    }
    assert "followgamerfleetonkick" in overlay_texts(frames)
    screen_text = ScreenText(frames=frames)
    # "Buy Phase" repeats "BUY PHASE" and is shown once.
    assert texts_between(screen_text, 0, 35) == ["KILLED BY REYNA", "BUY PHASE", "ACE"]
    assert texts_between(screen_text, 15, 25) == ["BUY PHASE"]
    assert keep_text("ACE") and not keep_text("38") and not keep_text("W")


@pytest.fixture(scope="module")
def video_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    assert FFMPEG is not None
    folder = tmp_path_factory.mktemp("ocr video")
    subprocess.run(  # noqa: S603 - fixed test command
        [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=20",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(folder / "source.mp4"),
        ],
        check=True,
    )  # fmt: skip
    return folder


@needs_ffmpeg
def test_read_frames_uses_the_reader_and_the_cache(video_dir: Path) -> None:
    seen: list[tuple[int, ...]] = []

    def reader(image: np.ndarray) -> list[tuple[str, float]]:
        seen.append(image.shape)
        return [("VICTORY", 0.95), ("blurry", 0.3), ("12", 0.99)]

    result = read_frames(video_dir, [2.0, 12.0], ffmpeg_path=FFMPEG, reader=reader)
    assert result.frames == {"2": ["VICTORY"], "12": ["VICTORY"]}
    assert seen[0][1] == 960 and seen[0][2] == 3  # 960 px wide, BGR
    assert load_text(video_dir) == result

    def never(_image: Any) -> list[tuple[str, float]]:
        raise AssertionError("cached frames are not read again")

    again = read_frames(video_dir, [2.0, 12.0], ffmpeg_path=FFMPEG, reader=never)
    assert again == result
