"""ORM models for the tables in docs/08-data-models.md.

Every change here needs a new Alembic migration in `app/db/migrations/versions/`.
Paths are stored relative to DATA_DIR. Timestamps are stored as UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

ID_LENGTH = 36  # UUID string


def utc_now() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """Stores aware datetimes as naive UTC and returns them as aware UTC.

    SQLite has no time zone type, so without this a read would lose the zone.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; pass an aware UTC datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"unexpected stored timestamp type {type(value).__name__}")
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class SettingRow(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)


class LlmCredentialRow(Base):
    """The one saved LLM setup (D-053). `api_key_enc` is Fernet-encrypted, never plain."""

    __tablename__ = "llm_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    base_url: Mapped[str | None] = mapped_column(String)
    api_key_enc: Mapped[str] = mapped_column(Text)
    saved_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)


class BatchRow(Base):
    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    status: Mapped[str] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(String)
    style_json: Mapped[str] = mapped_column(Text)
    # When the user last started work in this batch (created, More clips, Resume). The
    # sidebar history is ordered by it, newest first (D-055).
    last_activity_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utc_now, server_default="1970-01-01 00:00:00"
    )


class VideoRow(Base):
    __tablename__ = "videos"
    __table_args__ = (Index("ix_videos_batch_id", "batch_id"),)

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("batches.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(String)
    platform: Mapped[str | None] = mapped_column(String)
    source_id: Mapped[str | None] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(String)
    channel: Mapped[str | None] = mapped_column(String)
    duration_s: Mapped[float | None] = mapped_column(Float)
    thumbnail_path: Mapped[str | None] = mapped_column(String)
    has_chat: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String)
    stage: Mapped[str | None] = mapped_column(String)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error_code: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(String)
    source_path: Mapped[str | None] = mapped_column(String)
    # Which speech-to-text engine ran and why (D-028), shown under the video header.
    note: Mapped[str | None] = mapped_column(String)
    # The engine that ran, e.g. "Parakeet" or "Whisper medium" (D-030, temporary debug).
    transcribe_engine: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, onupdate=utc_now)


class ClipRow(Base):
    __tablename__ = "clips"
    __table_args__ = (
        Index("ix_clips_video_id", "video_id"),
        Index("ix_clips_batch_id", "batch_id"),
    )

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    video_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("videos.id", ondelete="CASCADE")
    )
    batch_id: Mapped[str] = mapped_column(
        String(ID_LENGTH), ForeignKey("batches.id", ondelete="CASCADE")
    )
    idx: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String)
    stage: Mapped[str | None] = mapped_column(String)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    start_s: Mapped[float] = mapped_column(Float)
    end_s: Mapped[float] = mapped_column(Float)
    title: Mapped[str | None] = mapped_column(String)
    youtube_title: Mapped[str | None] = mapped_column(String)
    youtube_description: Mapped[str | None] = mapped_column(Text)
    hashtags_json: Mapped[str | None] = mapped_column(Text)
    linkedin_post: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)
    caption_style: Mapped[str] = mapped_column(String)
    caption_language: Mapped[str] = mapped_column(String)
    crop_mode: Mapped[str] = mapped_column(String)
    blur: Mapped[int] = mapped_column(Integer)
    words_json: Mapped[str | None] = mapped_column(Text)
    # Manual blur boxes (D-048): JSON list of BlurBox.
    blur_boxes_json: Mapped[str | None] = mapped_column(Text)
    # Montage (D-051): the planned source ranges (JSON [[start, end], ...]) and whether the
    # clip plays only those (1) or the whole range from start to end (0).
    parts_json: Mapped[str | None] = mapped_column(Text)
    montage: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    edited: Mapped[int] = mapped_column(Integer, default=0)
    thumbnail_path: Mapped[str | None] = mapped_column(String)
    preview_path: Mapped[str | None] = mapped_column(String)
    export_path: Mapped[str | None] = mapped_column(String)
    error_code: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, onupdate=utc_now)


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_priority_created", "status", "priority", "created_at"),)

    id: Mapped[str] = mapped_column(String(ID_LENGTH), primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    # Points at a video or clip depending on `kind`, so it has no foreign key.
    ref_id: Mapped[str] = mapped_column(String(ID_LENGTH))
    status: Mapped[str] = mapped_column(String)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
