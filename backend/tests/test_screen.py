"""Screen type (D-040): label math, the stage on a synthetic video with a fake model, use in
candidates and fallback clips, and the model download."""

import io
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.config import SCREEN_FLOOR, SCREEN_MODEL_REVISION
from app.core.errors import AppError, ErrorCode
from app.core.hardware import MemoryInfo
from app.pipeline import screen as screen_module
from app.pipeline.candidates import Candidate, SignalScores, find_candidates
from app.pipeline.screen import (
    Labels,
    ScreenFile,
    describe,
    dull_series,
    ensure_model,
    group_shares,
    load_labels,
    load_screen,
    run_screen,
    screen_factor,
    window_shares,
)
from app.pipeline.select import VideoFacts, fallback_clip
from app.pipeline.signals import SignalFile

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")
MEMORY = MemoryInfo(total_gb=8.0, available_gb=4.0)
GROUPS = ["gameplay", "menu", "waiting", "talking", "other"]


def labels_2d() -> Labels:
    """Two labels on two axes: x = gameplay, y = menu."""
    return Labels(
        groups=["gameplay", "menu"],
        label_group=np.array([0, 1]),
        embeddings=np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
    )


def screen_file(rows: list[list[float]], step: float = 5) -> ScreenFile:
    return ScreenFile(revision=SCREEN_MODEL_REVISION, step_s=step, groups=GROUPS, shares=rows)


GAME = [0.9, 0.05, 0.0, 0.05, 0.0]
MENU = [0.1, 0.85, 0.05, 0.0, 0.0]


def test_shipped_labels_cover_the_groups() -> None:
    labels = load_labels()
    assert labels.groups == GROUPS
    assert labels.embeddings.shape == (len(labels.label_group), 512)
    norms = np.linalg.norm(labels.embeddings, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-3)


def test_group_shares_sum_to_one_and_pick_the_closest_label() -> None:
    images = np.array([[3.0, 0.1], [0.1, 3.0]], dtype=np.float32)
    shares = group_shares(images, labels_2d())
    assert np.allclose(shares.sum(axis=1), 1.0)
    assert shares[0, 0] > 0.99
    assert shares[1, 1] > 0.99


def test_dull_series_and_factor() -> None:
    screen = screen_file([GAME] * 12 + [MENU] * 12)
    dull = dull_series(screen, 120)
    assert dull[10] == pytest.approx(0.05, abs=0.01)
    assert dull[110] == pytest.approx(0.9, abs=0.01)
    factor = screen_factor(dull)
    assert factor[10] == 1.0
    assert factor[110] == pytest.approx(SCREEN_FLOOR)
    assert np.all(np.diff(factor) <= 1e-9)


def test_window_shares_and_description() -> None:
    screen = screen_file([GAME] * 4 + [MENU] * 4)
    shares = window_shares(screen, 0, 40)
    assert shares["gameplay"] == pytest.approx(0.5)
    assert shares["menu"] == pytest.approx(0.45)
    assert describe(shares) == "gameplay 50%, menu/lobby/loading 45%"
    assert window_shares(screen, 500, 560) == {}


def test_cached_file_of_another_model_revision_is_ignored(tmp_path: Path) -> None:
    folder = tmp_path / "signals"
    folder.mkdir()
    good = screen_file([GAME] * 12)
    (folder / "screen.json").write_text(good.model_dump_json(), encoding="utf-8")
    assert load_screen(tmp_path, 60.0) == good
    old = good.model_copy(update={"revision": "old"})
    (folder / "screen.json").write_text(old.model_dump_json(), encoding="utf-8")
    assert load_screen(tmp_path, 60.0) is None


def test_menu_peaks_lose_their_score_and_get_no_coverage() -> None:
    duration = 1200
    audio = [0.0] * duration
    for at in (400, 800):
        for offset in range(-4, 5):
            audio[at + offset] = 10.0 - abs(offset)
    # Menu from 700 s to 900 s, gameplay elsewhere.
    rows = [MENU if 700 <= i * 5 < 900 else GAME for i in range(duration // 5)]
    signals = {"audio": SignalFile(values=audio)}
    items = find_candidates(signals, float(duration), screen=screen_file(rows))
    by_peak = {item.peak_t: item for item in items if item.source == "signals"}
    assert by_peak[400.0].score > by_peak[800.0].score * 3
    assert by_peak[800.0].screen is not None
    assert by_peak[800.0].screen["menu"] > 0.5
    coverage = [item.peak_t for item in items if item.source == "coverage"]
    assert coverage
    assert all(not 700 <= peak < 900 for peak in coverage)


def test_no_fallback_clip_from_a_menu_window() -> None:
    facts = VideoFacts(title="t", channel=None, duration_s=1000.0)
    menu = Candidate(
        peak_t=125, start=100, end=160, score=0.5, signals=SignalScores(audio=0.5),
        screen={"gameplay": 0.2, "menu": 0.8},
    )  # fmt: skip
    game = menu.model_copy(update={"screen": {"gameplay": 0.9, "menu": 0.1}})
    assert fallback_clip(menu, facts, []) is None
    assert fallback_clip(game, facts, []) is not None


class _FakeSession:
    """Embeds each frame by its mean brightness: dark frames point at "menu"."""

    class _Input:
        name = "pixel_values"

    def get_inputs(self) -> list[Any]:
        return [self._Input()]

    def run(self, _outputs: Any, feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
        batch = feeds["pixel_values"]
        assert batch.shape[1:] == (3, 224, 224)
        bright = batch.mean(axis=(1, 2, 3))
        return [np.stack([bright, -bright], axis=1).astype(np.float32)]


@pytest.fixture(scope="module")
def video_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """20 s video: white for 10 s, then black."""
    assert FFMPEG is not None
    folder = tmp_path_factory.mktemp("screen video")
    subprocess.run(  # noqa: S603 - fixed test command
        [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=white:s=320x180:r=30:d=10",
            "-f", "lavfi", "-i", "color=c=black:s=320x180:r=30:d=10",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(folder / "source.mp4"),
        ],
        check=True,
    )  # fmt: skip
    return folder


@needs_ffmpeg
def test_stage_classifies_frames_and_caches(
    video_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(screen_module, "load_labels", labels_2d)
    monkeypatch.setattr(screen_module, "_session", lambda _model: _FakeSession())
    model = tmp_path / "models" / "screen" / SCREEN_MODEL_REVISION[:12] / "vision.onnx"
    monkeypatch.setattr(screen_module, "model_path", lambda _dir: model)
    model.parent.mkdir(parents=True)
    model.write_bytes(b"fake")
    seen: list[float] = []
    result = run_screen(
        video_dir, 20.0, tmp_path / "models", ffmpeg_path=FFMPEG, memory=MEMORY,
        on_progress=seen.append,
    )  # fmt: skip
    assert result.groups == ["gameplay", "menu"]
    assert len(result.shares) == 4
    assert result.shares[0][0] > 0.9  # white frame
    assert result.shares[-1][1] > 0.4  # black frame: no longer gameplay
    assert seen[-1] == 1.0
    assert load_screen(video_dir, 20.0) == result


class _Response(io.BytesIO):
    def __init__(self, body: bytes) -> None:
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}


def test_model_download_and_failure(tmp_path: Path) -> None:
    path = ensure_model(tmp_path, opener=lambda _url, _timeout: _Response(b"onnx"))
    assert path.read_bytes() == b"onnx"
    assert not path.with_name(path.name + ".part").exists()

    def offline(_url: str, _timeout: float) -> Any:
        raise OSError("no connection")

    with pytest.raises(AppError) as info:
        ensure_model(tmp_path / "other", opener=offline)
    assert info.value.code == ErrorCode.MODEL_DOWNLOAD_FAILED
