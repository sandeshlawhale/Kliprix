"""Backend settings read from environment variables, with documented defaults.

See docs/06-architecture.md ("Environment variables"). No secrets are read here:
API keys live in memory only and never pass through configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "Kliprix"
APP_VERSION = "0.1.0"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 8000
DEFAULT_ALLOWED_ORIGIN = "http://localhost:3000"
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_LOG_LEVEL = "INFO"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

# Hosts accepted for source links (docs/07, D-021: YouTube only in v1).
SUPPORTED_HOSTS = ("youtube.com", "youtu.be")

# Data layout below DATA_DIR (docs/08-data-models.md).
DATA_SUBDIRS = ("db", "downloads", "work", "previews", "thumbnails", "exports", "models", "logs")
DB_FILE_NAME = "app.sqlite"
LOG_FILE_NAME = "app.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

# SQLite connection pragma (docs/08): wait up to this long for a locked database.
SQLITE_BUSY_TIMEOUT_MS = 5000

# Timeout for quick tool probes such as `ffmpeg -version` or `nvidia-smi`.
PROBE_TIMEOUT_S = 15.0

BYTES_PER_GB = 1024**3

# Sources (docs/04 "Download", docs/07 "Videos").
MAX_VIDEOS_PER_BATCH = 3
MAX_URL_LENGTH = 2048
# Download height cap: 1080p normally, 720p when total RAM is below this (docs/04).
DOWNLOAD_MAX_HEIGHT = 1080
DOWNLOAD_LOW_RAM_MAX_HEIGHT = 720
DOWNLOAD_LOW_RAM_GB = 6.0
# Seconds yt-dlp waits on an unresponsive connection before retrying.
YTDLP_SOCKET_TIMEOUT_S = 30.0
# JavaScript runtime yt-dlp uses for YouTube's player challenges (needs Node 22+, AA-017).
YTDLP_JS_RUNTIME = "node"
# Disk check before download: estimated size times this factor plus a reserve; when
# YouTube reports no size, require DOWNLOAD_UNKNOWN_SIZE_MIN_FREE_GB (AA-019).
DOWNLOAD_SIZE_FACTOR = 1.1
DOWNLOAD_RESERVE_GB = 0.5
DOWNLOAD_UNKNOWN_SIZE_MIN_FREE_GB = 2.0
# `pip install --upgrade yt-dlp yt-dlp-ejs` for "Update downloader".
DOWNLOADER_UPDATE_TIMEOUT_S = 300.0
DOWNLOADER_PACKAGES = ("yt-dlp", "yt-dlp-ejs")


@dataclass(frozen=True)
class AppConfig:
    """Runtime configuration for one backend process."""

    backend_host: str
    backend_port: int
    allowed_origin: str
    data_dir: Path
    log_level: str
    ffmpeg_path: str | None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / DB_FILE_NAME

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


def resolve_data_dir(raw: str) -> Path:
    """Turn a DATA_DIR value into an absolute path.

    Relative paths are relative to the repo root, so `./data` means the same folder
    whether the backend starts from the repo root or from `backend/`.
    """
    if not raw.strip():
        return DEFAULT_DATA_DIR
    path = Path(raw.strip()).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def load_config() -> AppConfig:
    """Build the configuration from the environment.

    Raises:
        ValueError: if BACKEND_PORT is not an integer or LOG_LEVEL is unknown.
    """
    log_level = os.environ.get("LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
    if log_level not in LOG_LEVELS:
        raise ValueError(f"LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}")
    ffmpeg_path = os.environ.get("FFMPEG_PATH", "").strip()
    return AppConfig(
        backend_host=os.environ.get("BACKEND_HOST", DEFAULT_BACKEND_HOST),
        backend_port=int(os.environ.get("BACKEND_PORT", str(DEFAULT_BACKEND_PORT))),
        allowed_origin=os.environ.get("ALLOWED_ORIGIN", DEFAULT_ALLOWED_ORIGIN),
        data_dir=resolve_data_dir(os.environ.get("DATA_DIR", "")),
        log_level=log_level,
        ffmpeg_path=ffmpeg_path or None,
    )


def ensure_data_dirs(config: AppConfig) -> None:
    """Create DATA_DIR and its documented subfolders if missing."""
    for name in DATA_SUBDIRS:
        (config.data_dir / name).mkdir(parents=True, exist_ok=True)
