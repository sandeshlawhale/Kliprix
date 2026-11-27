import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.core import downloader_release
from app.core.hardware import MemoryInfo, pick_whisper_model
from app.main import create_app


@pytest.mark.parametrize(
    ("total", "available", "vram", "expected"),
    [
        (3.8, 2.0, None, "base"),  # 4 GB tier
        (3.8, 1.0, None, "tiny"),  # 4 GB tier, little free RAM
        (7.6, 3.0, None, "small"),  # "8 GB" machine reports a bit less
        (15.7, 4.0, None, "medium"),  # "16 GB" machine
        (3.8, 2.0, 2.0, "medium"),  # NVIDIA GPU below 4 GB VRAM
        (3.8, 2.0, 4.0, "large-v3-turbo"),  # NVIDIA GPU with 4 GB+ VRAM (D-032)
        (7.6, 3.0, 12.0, "large-v3-turbo"),
    ],
)
def test_whisper_auto_model_tiers(
    total: float, available: float, vram: float | None, expected: str
) -> None:
    assert pick_whisper_model(MemoryInfo(total, available), vram) == expected


def test_system_reports_real_values(client: TestClient) -> None:
    response = client.get("/api/system")
    assert response.status_code == 200
    body = response.json()
    assert body["supported_hosts"] == ["youtube.com", "youtu.be"]
    assert body["ram_gb"] > 0
    assert body["disk_free_gb"] > 0
    assert body["python"].count(".") == 2
    assert body["whisper_auto_model"] in {"tiny", "base", "small", "medium", "large-v3-turbo"}
    # ffmpeg presence must match the real machine, not a canned answer.
    assert body["ffmpeg"]["found"] == (shutil.which("ffmpeg") is not None)


def test_system_reports_missing_ffmpeg_path(config: AppConfig, tmp_path: Path) -> None:
    missing = replace(config, ffmpeg_path=str(tmp_path / "no ffmpeg" / "ffmpeg.exe"))
    with TestClient(create_app(missing)) as client:
        body = client.get("/api/system").json()
    assert body["ffmpeg"] == {"found": False, "version": None, "libass": False}


@pytest.mark.parametrize(
    ("latest", "installed", "expected"),
    [
        ("2026.11.20", "2026.10.26", True),
        ("2026.10.26.1", "2026.10.26", True),
        ("2026.10.26", "2026.10.26", False),
        ("2026.10.26", "2026.11.1", False),
        (None, "2026.10.26", False),
        ("2026.11.20", None, False),
        ("not-a-version", "2026.10.26", False),
    ],
)
def test_downloader_is_newer(latest: str | None, installed: str | None, expected: bool) -> None:
    assert downloader_release.is_newer(latest, installed) is expected


def test_downloader_release_is_cached() -> None:
    calls: list[int] = []

    def fake() -> str | None:
        calls.append(1)
        return "2099.1.1"

    downloader_release.clear_cache()
    try:
        assert downloader_release.latest_version(fake) == "2099.1.1"
        assert downloader_release.latest_version(fake) == "2099.1.1"
        assert len(calls) == 1
    finally:
        downloader_release.clear_cache()


def test_downloader_release_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    downloader_release.clear_cache()
    monkeypatch.setattr(downloader_release, "fetch_latest", lambda: "2099.1.1")
    try:
        body = client.get("/api/system/downloader-release").json()
    finally:
        downloader_release.clear_cache()
    assert body["latest"] == "2099.1.1"
    assert body["update_available"] is (body["installed"] is not None)


def test_downloader_release_offline(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader_release, "latest_version", lambda: None)
    body = client.get("/api/system/downloader-release").json()
    assert body == {"installed": body["installed"], "latest": None, "update_available": False}
