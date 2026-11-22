"""Downloads of model files at first use (Parakeet D-028, screen model D-040, sound model D-041).

A file is written to `<name>.part` and renamed only when complete, so a cut-off download never
looks like a finished model.
"""

from __future__ import annotations

import logging
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.config import MODEL_DOWNLOAD_BLOCK_BYTES, MODEL_DOWNLOAD_TIMEOUT_S
from app.core.errors import AppError, ErrorCode
from app.core.hardware import disk_free_gb
from app.core.proc import CommandCancelledError

logger = logging.getLogger(__name__)

# Called with the fraction (0.0-1.0) of the file downloaded so far.
DownloadProgress = Callable[[float], None]
Opener = Callable[[str, float], Any]


def open_url(url: str, timeout_s: float) -> Any:
    return urllib.request.urlopen(url, timeout=timeout_s)  # noqa: S310 - fixed https model URL


def download_stream(
    url: str,
    target: Path,
    cancel: threading.Event | None,
    on_progress: DownloadProgress | None,
    opener: Opener,
) -> None:
    """Write the response body of `url` to `target` block by block.

    Raises:
        OSError: an incomplete download (fewer bytes than Content-Length).
        urllib.error.URLError: no connection.
        CommandCancelledError: `cancel` was set.
    """
    with opener(url, MODEL_DOWNLOAD_TIMEOUT_S) as response, target.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancel is not None and cancel.is_set():
                raise CommandCancelledError("model download was cancelled")
            block = response.read(MODEL_DOWNLOAD_BLOCK_BYTES)
            if not block:
                break
            out.write(block)
            done += len(block)
            if on_progress is not None and total:
                on_progress(min(1.0, done / total))
    if total and done != total:
        raise OSError(f"incomplete download: {done} of {total} bytes")


def ensure_file(
    url: str,
    target: Path,
    *,
    label: str,
    disk_needed_gb: float,
    cancel: threading.Event | None = None,
    on_progress: DownloadProgress | None = None,
    opener: Opener | None = None,
) -> Path:
    """`target`, downloading it from `url` first when it is missing.

    Raises:
        AppError MODEL_DOWNLOAD_FAILED: no connection or an incomplete download.
        AppError DISK_FULL: less than `disk_needed_gb` free.
        CommandCancelledError: `cancel` was set during the download.
    """
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    free = disk_free_gb(target.parent)
    if free < disk_needed_gb:
        raise AppError(
            ErrorCode.DISK_FULL,
            f"Not enough free disk space to download the {label}.",
            {"needed_gb": disk_needed_gb, "free_gb": round(free, 1)},
        )
    partial = target.with_name(target.name + ".part")
    logger.info("model download started label=%s url=%s", label, url)
    try:
        download_stream(url, partial, cancel, on_progress, opener or open_url)
        partial.replace(target)
    except (urllib.error.URLError, OSError) as exc:
        logger.error("model download failed label=%s: %s", label, exc)
        raise AppError(
            ErrorCode.MODEL_DOWNLOAD_FAILED,
            f"The {label} could not be downloaded. Check the internet connection; the next "
            "video tries again.",
        ) from exc
    finally:
        partial.unlink(missing_ok=True)
    logger.info("model ready label=%s path=%s", label, target)
    return target
