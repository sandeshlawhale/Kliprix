"""System endpoints (docs/07-api-contract.md, "System")."""

from __future__ import annotations

import platform
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import ConfigDep
from app.config import APP_VERSION, SUPPORTED_HOSTS
from app.core import hardware
from app.core.errors import ERROR_RESPONSES

router = APIRouter(prefix="/api", tags=["system"], responses=ERROR_RESPONSES)

GB_DECIMALS = 1


class HealthResponse(BaseModel):
    """Liveness answer: the process is up and serving requests."""

    status: Literal["ok"]
    version: str


class FfmpegStatus(BaseModel):
    found: bool
    version: str | None
    libass: bool


class SystemInfo(BaseModel):
    """Real facts about this machine, measured on every request."""

    ffmpeg: FfmpegStatus
    python: str
    ram_gb: float
    gpu: bool
    whisper_auto_model: hardware.WhisperModel
    supported_hosts: list[str]
    disk_free_gb: float
    ytdlp_version: str | None


@router.get("/health")
def get_health() -> HealthResponse:
    """Return `{status: "ok", version}` while the backend is running."""
    return HealthResponse(status="ok", version=APP_VERSION)


@router.get("/system")
def get_system(config: ConfigDep) -> SystemInfo:
    """ffmpeg/libass status, Python, RAM, GPU, Whisper Auto model, disk, yt-dlp version."""
    memory = hardware.read_memory()
    vram = hardware.nvidia_vram_gb()
    ffmpeg = hardware.probe_ffmpeg(config.ffmpeg_path)
    return SystemInfo(
        ffmpeg=FfmpegStatus(found=ffmpeg.found, version=ffmpeg.version, libass=ffmpeg.libass),
        python=platform.python_version(),
        ram_gb=round(memory.total_gb, GB_DECIMALS),
        gpu=vram is not None,
        whisper_auto_model=hardware.pick_whisper_model(memory, vram),
        supported_hosts=list(SUPPORTED_HOSTS),
        disk_free_gb=round(hardware.disk_free_gb(config.data_dir), GB_DECIMALS),
        ytdlp_version=hardware.ytdlp_version(),
    )
