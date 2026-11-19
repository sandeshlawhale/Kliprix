"""What job handlers need: config, database, sources, LLM session, event bus, and paths."""

from __future__ import annotations

import re
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.config import PARAKEET_ONLY, AppConfig
from app.core.events import Event, EventBus
from app.core.hardware import MemoryInfo, read_memory
from app.llm.client import Completion, default_completion
from app.llm.session import LlmSessionStore
from app.pipeline.transcribe import RunnerFactory
from app.sources.base import SourceAdapter

SLUG_MAX = 40
_NOT_SLUG = re.compile(r"[^a-z0-9]+")
# Builds the Whisper runner for a model name and GPU flag (replaced by a fake in tests).
WhisperFactory = Callable[[str, bool], RunnerFactory]
# Builds the Parakeet runner (D-028) for a job's cancel event and a model-download progress
# callback. None = this app has no Parakeet engine, so Whisper is used.
ParakeetFactory = Callable[[threading.Event | None, Callable[[float], None]], RunnerFactory]


def slugify(text: str, fallback: str = "clip") -> str:
    """ASCII, lowercase, dashes, at most 40 characters (docs/08: slugified names)."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = _NOT_SLUG.sub("-", ascii_text.lower()).strip("-")[:SLUG_MAX].strip("-")
    return slug or fallback


@dataclass
class JobContext:
    config: AppConfig
    session_factory: sessionmaker[Session]
    adapters: list[SourceAdapter]
    llm: LlmSessionStore
    bus: EventBus
    whisper_factory: WhisperFactory
    completion: Completion = default_completion
    memory: Callable[[], MemoryInfo] = read_memory
    gpu_vram: Callable[[], float | None] = field(default=lambda: None)
    parakeet_factory: ParakeetFactory | None = None
    # Experiment D-030: Parakeet for every video, Whisper switched off (tests turn it off).
    parakeet_only: bool = PARAKEET_ONLY

    @property
    def data_dir(self) -> Path:
        return self.config.data_dir

    def video_dir(self, video_id: str) -> Path:
        return self.data_dir / "downloads" / video_id

    def clip_work_dir(self, clip_id: str) -> Path:
        return self.data_dir / "work" / clip_id

    def relative(self, path: Path) -> str:
        """A path stored in the DB: relative to DATA_DIR, with forward slashes."""
        return path.relative_to(self.data_dir).as_posix()

    def absolute(self, stored: str) -> Path:
        return self.data_dir / stored

    def publish(self, batch_id: str, kind: str, **data: Any) -> None:
        self.bus.publish(batch_id, Event(kind, data))


@dataclass
class CancelToken:
    """The cancel event of the running job, plus which refs it covers."""

    job_id: str
    ref_id: str
    batch_id: str
    event: threading.Event = field(default_factory=threading.Event)
