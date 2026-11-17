"""Facts about this machine: RAM, NVIDIA GPU, ffmpeg, disk, and the Whisper Auto choice.

Tiers come from docs/11-constraints.md ("Hardware targets").
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Literal

import psutil

from app.config import BYTES_PER_GB, PROBE_TIMEOUT_S
from app.core.proc import CommandError, run_command

logger = logging.getLogger(__name__)

WhisperModel = Literal["tiny", "base", "small", "medium", "large-v3"]

# Nominal RAM tiers in GB. Machines report a bit less than their nominal size,
# so a tier counts as reached at this fraction of its nominal value.
RAM_TIER_NORMAL_GB = 8.0
RAM_TIER_GOOD_GB = 16.0
RAM_TIER_TOLERANCE = 0.9
LOW_FREE_RAM_GB = 1.5
LARGE_MODEL_MIN_VRAM_GB = 8.0
MIB_PER_GB = 1024


@dataclass(frozen=True)
class MemoryInfo:
    total_gb: float
    available_gb: float


@dataclass(frozen=True)
class FfmpegInfo:
    found: bool
    version: str | None
    libass: bool


def read_memory() -> MemoryInfo:
    memory = psutil.virtual_memory()
    return MemoryInfo(memory.total / BYTES_PER_GB, memory.available / BYTES_PER_GB)


def nvidia_vram_gb() -> float | None:
    """Largest NVIDIA GPU memory in GB, or None when no NVIDIA GPU/driver is present."""
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return None
    args = [smi, "--query-gpu=memory.total", "--format=csv,noheader,nounits"]
    try:
        result = run_command(args, PROBE_TIMEOUT_S)
    except CommandError as exc:
        logger.warning("nvidia-smi failed: %s", exc)
        return None
    if result.returncode != 0:
        logger.warning("nvidia-smi exited with %s", result.returncode)
        return None
    sizes_mib = [float(line) for line in result.stdout.split() if _is_number(line)]
    return max(sizes_mib) / MIB_PER_GB if sizes_mib else None


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def pick_whisper_model(memory: MemoryInfo, gpu_vram_gb: float | None) -> WhisperModel:
    """The model "Auto" uses on this machine (docs/11 hardware table)."""
    if gpu_vram_gb is not None:
        return "large-v3" if gpu_vram_gb >= LARGE_MODEL_MIN_VRAM_GB else "medium"
    if memory.total_gb >= RAM_TIER_GOOD_GB * RAM_TIER_TOLERANCE:
        return "medium"
    if memory.total_gb >= RAM_TIER_NORMAL_GB * RAM_TIER_TOLERANCE:
        return "small"
    return "tiny" if memory.available_gb < LOW_FREE_RAM_GB else "base"


def find_ffmpeg(ffmpeg_path: str | None) -> str | None:
    """FFMPEG_PATH when set and present, otherwise `ffmpeg` on PATH."""
    if ffmpeg_path:
        return ffmpeg_path if Path(ffmpeg_path).is_file() else None
    return shutil.which("ffmpeg")


def probe_ffmpeg(ffmpeg_path: str | None) -> FfmpegInfo:
    """Whether ffmpeg runs, its version string, and whether it has libass filters."""
    path = find_ffmpeg(ffmpeg_path)
    if path is None:
        return FfmpegInfo(found=False, version=None, libass=False)
    try:
        version = run_command([path, "-hide_banner", "-version"], PROBE_TIMEOUT_S)
        filters = run_command([path, "-hide_banner", "-filters"], PROBE_TIMEOUT_S)
    except CommandError as exc:
        logger.warning("ffmpeg probe failed: %s", exc)
        return FfmpegInfo(found=False, version=None, libass=False)
    if version.returncode != 0:
        return FfmpegInfo(found=False, version=None, libass=False)
    first_line = version.stdout.splitlines()[0] if version.stdout else ""
    # "ffmpeg version 9.0.1-essentials_build-www.gyan.dev Copyright ..." -> "9.0.1-..."
    words = first_line.split()
    version_text = words[2] if len(words) > 2 and words[1] == "version" else None
    names = {line.split()[1] for line in filters.stdout.splitlines() if len(line.split()) > 1}
    # The `ass` and `subtitles` filters only exist when ffmpeg is built with libass.
    return FfmpegInfo(found=True, version=version_text, libass={"ass", "subtitles"} <= names)


def disk_free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / BYTES_PER_GB


def ytdlp_version() -> str | None:
    """Installed yt-dlp version, or None when it is not installed."""
    try:
        return metadata.version("yt-dlp")
    except metadata.PackageNotFoundError:
        return None
