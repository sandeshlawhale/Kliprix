"""Whisper on the GPU (D-031, D-032): Auto picks large-v3-turbo on a 4 GB+ NVIDIA GPU; when the
GPU cannot load the model, the runner loads the CPU model instead of turbo on the CPU."""

import sys
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar

import pytest

from app.pipeline.transcribe import WhisperRunner


class FakeWhisperModel:
    loaded: ClassVar[list[tuple[str, str]]] = []

    def __init__(self, name: str, *, device: str, compute_type: str, download_root: str) -> None:
        if device == "cuda":
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        FakeWhisperModel.loaded.append((name, device))


def test_gpu_failure_loads_the_cpu_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = ModuleType("faster_whisper")
    fake.WhisperModel = FakeWhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    FakeWhisperModel.loaded = []
    runner = WhisperRunner("large-v3-turbo", tmp_path, use_gpu=True, cpu_model="medium")
    assert (runner.device, runner.model_name) == ("cpu", "medium")
    assert FakeWhisperModel.loaded == [("medium", "cpu")]


def test_without_cpu_model_the_same_model_runs_on_the_cpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake: Any = ModuleType("faster_whisper")
    fake.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    FakeWhisperModel.loaded = []
    runner = WhisperRunner("small", tmp_path, use_gpu=False)
    assert (runner.device, runner.model_name) == ("cpu", "small")
