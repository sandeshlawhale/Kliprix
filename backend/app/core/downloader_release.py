"""Latest yt-dlp release on PyPI, so Settings can show that an update is available (D-068).

One anonymous GET to PyPI's JSON API, only when the frontend asks (the Settings tab is open),
remembered in memory for a few hours. Any failure means "unknown", never an error.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable

logger = logging.getLogger(__name__)

PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"
FETCH_TIMEOUT_S = 5.0
CACHE_S = 6 * 60 * 60
# PyPI JSON for yt-dlp is about 1 MB with every release listed; stop reading well past it.
MAX_BYTES = 8 * 1024 * 1024

_lock = threading.Lock()
_cached: tuple[float, str | None] | None = None


def fetch_latest() -> str | None:
    """The newest yt-dlp version on PyPI, or None when PyPI can't be reached or read."""
    request = urllib.request.Request(PYPI_URL, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:  # noqa: S310 - fixed https URL
            body = json.loads(response.read(MAX_BYTES))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.info("yt-dlp release check failed: %s", exc)
        return None
    version = body.get("info", {}).get("version") if isinstance(body, dict) else None
    return version if isinstance(version, str) and version else None


def latest_version(fetch: Callable[[], str | None] | None = None) -> str | None:
    """Latest version, fetched at most once per CACHE_S (a failed fetch is cached too)."""
    global _cached
    with _lock:
        now = time.monotonic()
        if _cached is not None and now - _cached[0] < CACHE_S:
            return _cached[1]
        latest = (fetch or fetch_latest)()
        _cached = (now, latest)
        return latest


def clear_cache() -> None:
    global _cached
    with _lock:
        _cached = None


def version_key(version: str) -> tuple[int, ...] | None:
    """yt-dlp versions are dates with an optional build number: 2026.10.26 or 2026.10.26.1."""
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def is_newer(latest: str | None, installed: str | None) -> bool:
    """True only when both versions are known, readable and latest is higher."""
    if latest is None or installed is None:
        return False
    new, old = version_key(latest), version_key(installed)
    return new is not None and old is not None and new > old
