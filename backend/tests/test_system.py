import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.core.hardware import MemoryInfo, pick_whisper_model
from app.main import create_app


@pytest.mark.parametrize(
    ("total", "available", "vram", "expected"),
    [
        (3.8, 2.0, None, "base"),  # 4 GB tier
        (3.8, 1.0, None, "tiny"),  # 4 GB tier, little free RAM
        (7.6, 3.0, None, "small"),  # "8 GB" machine reports a bit less
        (15.7, 4.0, None, "medium"),  # "16 GB" machine
        (3.8, 2.0, 6.0, "medium"),  # NVIDIA GPU below 8 GB VRAM
        (7.6, 3.0, 12.0, "large-v3"),  # NVIDIA GPU with 8 GB+ VRAM
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
    assert body["whisper_auto_model"] in {"tiny", "base", "small", "medium", "large-v3"}
    # ffmpeg presence must match the real machine, not a canned answer.
    assert body["ffmpeg"]["found"] == (shutil.which("ffmpeg") is not None)


def test_system_reports_missing_ffmpeg_path(config: AppConfig, tmp_path: Path) -> None:
    missing = replace(config, ffmpeg_path=str(tmp_path / "no ffmpeg" / "ffmpeg.exe"))
    with TestClient(create_app(missing)) as client:
        body = client.get("/api/system").json()
    assert body["ffmpeg"] == {"found": False, "version": None, "libass": False}
