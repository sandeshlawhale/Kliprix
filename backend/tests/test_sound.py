"""Sound events (D-041): grouping, the per-second signal, tagging a WAV with a fake model,
caching, the prompt timeline, and the model archive download."""

import io
import tarfile
import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.config import AUDIO_SAMPLE_RATE
from app.core.errors import AppError, ErrorCode
from app.core.model_download import ensure_archive
from app.pipeline import sound as sound_module
from app.pipeline.sound import (
    SoundEvents,
    excitement,
    group_values,
    load_events,
    per_second,
    run_sound,
    tag_wav,
    timeline,
    window_share,
)


def test_groups_take_their_strongest_class() -> None:
    groups = group_values({"Machine gun": 0.6, "Gunshot, gunfire": 0.3, "Laughter": 0.2})
    assert groups["gunfire"] == 0.6
    assert groups["laughter"] == 0.2
    assert groups["explosion"] == 0.0


def test_excitement_ignores_speech_and_music() -> None:
    assert excitement({"speech": 0.9, "music": 0.9}) == 0.0
    assert excitement({"gunfire": 0.5, "vehicle": 0.5}) == pytest.approx(0.5 + 0.2)


def test_per_second_repeats_each_window() -> None:
    values = per_second([{"gunfire": 0.5}, {}, {"shouting": 1.0}], 2.0, 7)
    assert values == [0.5, 0.5, 0.0, 0.0, 1.0, 1.0, 0.0]


def test_timeline_merges_repeats_and_skips_background() -> None:
    events = SoundEvents(
        window_s=2,
        windows=[
            {"speech": 0.9},
            {"gunfire": 0.6},
            {"gunfire": 0.7},
            {"gunfire": 0.5, "shouting": 0.8},
            {"music": 0.9},
            {"laughter": 0.3},
        ],
    )
    assert timeline(events, 0, 12) == [
        (2.0, "gunfire"),
        (6.0, "shouting, gunfire"),
        (10.0, "laughter"),
    ]
    assert timeline(events, 8, 12) == [(10.0, "laughter")]
    assert window_share(events, 2, 8, "gunfire") == pytest.approx(0.6)


def _wav(path: Path, seconds: int) -> Path:
    """Silence, then a loud second half."""
    samples = np.zeros(seconds * AUDIO_SAMPLE_RATE, dtype=np.int16)
    samples[len(samples) // 2 :] = 20000
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(AUDIO_SAMPLE_RATE)
        writer.writeframes(samples.tobytes())
    return path


def _fake_tag(samples: np.ndarray) -> dict[str, float]:
    assert samples.dtype == np.float32
    # The real model fails on short input, so every window has its full length.
    assert len(samples) == 2 * AUDIO_SAMPLE_RATE
    loud = float(np.abs(samples).mean())
    return {"Explosion": round(min(1.0, loud), 2), "Speech": 0.05}


def test_tag_wav_window_by_window(tmp_path: Path) -> None:
    seen: list[float] = []
    events = tag_wav(_wav(tmp_path / "a.wav", 9), _fake_tag, on_progress=seen.append)
    assert len(events.windows) == 5
    assert events.windows[0] == {}  # silence; speech 0.05 is below the save minimum
    assert events.windows[-2]["explosion"] > 0.5
    assert 0 < events.windows[-1]["explosion"] < events.windows[-2]["explosion"]  # padded
    assert seen[-1] == 1.0


def test_run_sound_writes_both_files_and_reuses_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def tagger(_folder: Path) -> Any:
        calls.append(1)
        return _fake_tag

    monkeypatch.setattr(sound_module, "ensure_model", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(sound_module, "make_tagger", tagger)
    wav = _wav(tmp_path / "audio.wav", 8)
    signal, events = run_sound(tmp_path, wav, 8.0, tmp_path / "models")
    assert len(signal.values) == 8
    assert signal.values[0] == 0.0
    assert signal.values[-1] > 0.5
    assert load_events(tmp_path, 8.0) == events
    again, _ = run_sound(tmp_path, wav, 8.0, tmp_path / "models")
    assert again == signal
    assert calls == [1]


class _Response(io.BytesIO):
    def __init__(self, body: bytes) -> None:
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}


def _archive(folder: str, files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(f"{folder}/{name}")
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


def test_archive_download_unpacks_into_place(tmp_path: Path) -> None:
    body = _archive("m", {"model.onnx": b"x", "labels.csv": b"y"})
    folder = ensure_archive(
        "https://example.invalid/m.tar.bz2", tmp_path, "m", ("model.onnx", "labels.csv"),
        label="test model", disk_needed_gb=0.0, opener=lambda _u, _t: _Response(body),
    )  # fmt: skip
    assert (folder / "model.onnx").read_bytes() == b"x"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m"]


def test_archive_without_the_files_fails_cleanly(tmp_path: Path) -> None:
    body = _archive("m", {"other.txt": b"x"})
    with pytest.raises(AppError) as info:
        ensure_archive(
            "https://example.invalid/m.tar.bz2", tmp_path, "m", ("model.onnx",),
            label="test model", disk_needed_gb=0.0, opener=lambda _u, _t: _Response(body),
        )  # fmt: skip
    assert info.value.code == ErrorCode.MODEL_DOWNLOAD_FAILED
    assert list(tmp_path.iterdir()) == []
