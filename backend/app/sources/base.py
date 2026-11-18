"""The source adapter interface (docs/06-architecture.md): `inspect`, `download`, `get_chat`.

Each platform implements `SourceAdapter`; the rest of the backend only sees these
types, so adding Twitch or Kick means one adapter file plus a host allowlist entry.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import SplitResult

from pydantic import BaseModel, ConfigDict, Field

# Called with the fraction (0.0-1.0) of the download done so far.
ProgressCallback = Callable[[float], None]

SOURCE_FILE_NAME = "source.mp4"


class VideoMeta(BaseModel):
    """What `inspect` learns about a video without downloading it (docs/07 `video`)."""

    model_config = ConfigDict(extra="forbid")

    platform: str
    source_id: str
    title: str
    channel: str | None
    duration_s: float = Field(gt=0)
    thumbnail_url: str | None
    has_chat: bool
    is_live: bool


class ChatMessage(BaseModel):
    """One chat replay message; `t` is seconds from the start of the video."""

    model_config = ConfigDict(extra="forbid")

    t: float = Field(ge=0)
    text: str


class SourceCancelledError(Exception):
    """The caller's cancel event was set while the adapter was working."""


class SourceAdapter(Protocol):
    """One video platform. Methods raise `AppError` with the documented codes."""

    platform: str
    hosts: tuple[str, ...]

    def canonical_url(self, parts: SplitResult) -> str:
        """The single-video URL the adapter will fetch (drops playlists and tracking)."""
        ...

    def inspect(self, url: str) -> VideoMeta:
        """Metadata for `url` without downloading media. Rejects live/upcoming streams."""
        ...

    def download(
        self,
        url: str,
        dest_dir: Path,
        *,
        max_height: int,
        cancel: threading.Event | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> tuple[Path, VideoMeta]:
        """Download video+audio merged into `dest_dir/source.mp4`; return it and its metadata."""
        ...

    def get_chat(
        self, url: str, work_dir: Path, *, cancel: threading.Event | None = None
    ) -> list[ChatMessage] | None:
        """Chat replay messages sorted by time, or None when the video has no replay."""
        ...
