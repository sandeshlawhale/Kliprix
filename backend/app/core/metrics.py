"""Stage timing and peak-memory logging (docs/11: measure and log durations and peak memory)."""

from __future__ import annotations

import logging
import sys
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
