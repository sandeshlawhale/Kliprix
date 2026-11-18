"""Stage timing and peak-memory logging (docs/11: measure and log durations and peak memory)."""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import psutil

from app.config import BYTES_PER_GB

logger = logging.getLogger(__name__)


def peak_memory_gb() -> float:
    """Peak resident memory of this process so far, in GB."""
    if sys.platform == "win32":
        return float(psutil.Process().memory_info().peak_wset) / BYTES_PER_GB
    import resource  # Unix only

    # ru_maxrss is KiB on Linux, bytes on macOS.
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    scale = 1 if sys.platform == "darwin" else 1024
    return float(maxrss * scale) / BYTES_PER_GB


@contextmanager
def stage_timer(stage: str, **context: object) -> Iterator[None]:
    """Log `stage done` with its duration and the process peak memory when the block ends."""
    started = time.monotonic()
    yield
    extra = " ".join(f"{key}={value}" for key, value in context.items())
    logger.info(
        "stage done stage=%s %s duration_s=%.1f peak_mem_gb=%.2f",
        stage,
        extra,
        time.monotonic() - started,
        peak_memory_gb(),
    )


class TreeMemorySampler:
    """Samples the resident memory of this process plus its children (ffmpeg) in a thread
    and keeps the peak, so subprocess memory is measured too."""

    def __init__(self, interval_s: float = 0.2) -> None:
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.peak_gb = 0.0

    def _run(self) -> None:
        me = psutil.Process()
        while not self._stop.wait(self._interval_s):
            total = 0
            for process in [me, *me.children(recursive=True)]:
                try:
                    total += process.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            self.peak_gb = max(self.peak_gb, total / BYTES_PER_GB)

    def __enter__(self) -> TreeMemorySampler:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()
