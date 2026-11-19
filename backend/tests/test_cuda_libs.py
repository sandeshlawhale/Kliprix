"""GPU library registration for Whisper (D-031): found folders are registered once, and a PC
without the NVIDIA packages registers nothing (Whisper then uses the CPU)."""

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core import cuda_libs


def _fake_nvidia(root: Path) -> SimpleNamespace:
    for name in ("cublas", "cuda_nvrtc"):
        (root / name / "bin").mkdir(parents=True)
        (root / name / "lib").mkdir(parents=True)
    (root / "not_a_package.txt").write_text("x")
    return SimpleNamespace(submodule_search_locations=[str(root)])


def test_windows_folders_are_added_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _fake_nvidia(tmp_path)
    added: list[str] = []
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: spec)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "add_dll_directory", added.append, raising=False)
    cuda_libs.register_cuda_libraries.cache_clear()
    try:
        folders = cuda_libs.register_cuda_libraries()
        cuda_libs.register_cuda_libraries()  # cached: nothing added twice
    finally:
        cuda_libs.register_cuda_libraries.cache_clear()
    expected = [str(tmp_path / "cublas" / "bin"), str(tmp_path / "cuda_nvrtc" / "bin")]
    assert [str(f) for f in folders] == expected
    assert added == expected


def test_nothing_to_register_without_the_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> Any:
        return None

    monkeypatch.setattr(importlib.util, "find_spec", missing)
    cuda_libs.register_cuda_libraries.cache_clear()
    try:
        assert cuda_libs.register_cuda_libraries() == []
    finally:
        cuda_libs.register_cuda_libraries.cache_clear()
