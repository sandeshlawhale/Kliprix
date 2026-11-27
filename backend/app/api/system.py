"""System endpoints (docs/07-api-contract.md, "System")."""

from __future__ import annotations

import logging
import platform
import sys
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import ConfigDep
from app.config import (
    APP_VERSION,
    DOWNLOADER_PACKAGES,
    DOWNLOADER_UPDATE_TIMEOUT_S,
    SUPPORTED_HOSTS,
)
from app.core import downloader_release, hardware
from app.core.errors import ERROR_RESPONSES, AppError, ErrorCode
from app.core.proc import CommandError, run_command

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["system"], responses=ERROR_RESPONSES)

GB_DECIMALS = 1
# Characters of pip's stderr kept in the log when an update fails.
PIP_LOG_TAIL = 2000


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


class DownloaderRelease(BaseModel):
    """Installed and newest yt-dlp versions; latest is None when PyPI could not be reached."""

    installed: str | None
    latest: str | None
    update_available: bool


@router.get("/system/downloader-release")
def get_downloader_release() -> DownloaderRelease:
    """Compare the installed yt-dlp with the newest on PyPI (checked at most every 6 h, D-068).

    Never fails: without a connection `latest` is null and `update_available` false.
    """
    installed = hardware.ytdlp_version()
    latest = downloader_release.latest_version()
    return DownloaderRelease(
        installed=installed,
        latest=latest,
        update_available=downloader_release.is_newer(latest, installed),
    )


class DownloaderUpdate(BaseModel):
    """Installed yt-dlp version before and after the upgrade."""

    before: str | None
    after: str | None


@router.post("/system/update-downloader")
def update_downloader() -> DownloaderUpdate:
    """Upgrade yt-dlp and its YouTube scripts with pip in the backend's own environment.

    The running backend keeps using the already imported yt-dlp until it restarts (AA-021).

    Errors: DOWNLOAD_FAILED when pip fails or times out.
    """
    before = hardware.ytdlp_version()
    args = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--disable-pip-version-check",
        *DOWNLOADER_PACKAGES,
    ]
    try:
        result = run_command(args, DOWNLOADER_UPDATE_TIMEOUT_S)
    except CommandError as exc:
        logger.warning("downloader update failed: %s", exc)
        raise AppError(
            ErrorCode.DOWNLOAD_FAILED, "Updating the downloader failed. See the log file."
        ) from exc
    if result.returncode != 0:
        logger.warning(
            "downloader update failed exit=%s stderr=%s",
            result.returncode,
            result.stderr[-PIP_LOG_TAIL:],
        )
        raise AppError(
            ErrorCode.DOWNLOAD_FAILED,
            "Updating the downloader failed. Check your internet connection and the log file.",
        )
    after = hardware.ytdlp_version()
    logger.info("downloader updated before=%s after=%s", before, after)
    return DownloaderUpdate(before=before, after=after)
