"""Response models for batches and clips (docs/07) and the functions that build them from
rows. Shared by the batches and clips routers."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.errors import ErrorBody, ErrorCode
from app.core.settings_schema import CaptionLanguage, CaptionStyle, CropMode
from app.db.models import BatchRow, ClipRow, VideoRow
from app.db.repositories import batches as repo
from app.jobs.clip_jobs import played_parts
from app.jobs.context import JobContext
from app.jobs.state import checkpoints, more_clips_blocker
from app.pipeline.cut import load_cut
from app.pipeline.download import INFO_FILE_NAME
from app.pipeline.parts import total
from app.render.censor import BlurBox
from app.sources.base import SOURCE_FILE_NAME

CheckpointKey = Literal["download", "chat", "signals", "candidates", "transcribe", "select", "edit"]
CheckpointStateName = Literal["pending", "running", "paused", "done", "skipped", "failed"]


class Checkpoint(BaseModel):
    key: CheckpointKey
    label: str
    state: CheckpointStateName
    detail: str | None = None
    # Transcribe step only: the engine that ran or runs, e.g. "Parakeet" (D-030, temporary).
    engine: str | None = None


class ClipSummary(BaseModel):
    id: str
    index: int
    status: str
    stage: str | None
    progress: float
    queue_position: int | None = None
    title: str
    start_s: float
    end_s: float
    duration_s: float
    thumbnail_url: str | None = None
    error: ErrorBody | None = None
    edited: bool
    has_preview: bool
    has_export: bool
    # Changes on every update; the UI uses it to reload media after a re-render.
    updated_at: datetime


class Video(BaseModel):
    id: str
    url: str
    platform: str | None
    title: str | None
    channel: str | None
    duration_s: float | None
    thumbnail_url: str | None
    has_chat: bool
    status: str
    stage: str | None
    progress: float
    error: ErrorBody | None = None
    # Which speech-to-text engine ran and why (D-028), e.g. "Speech-to-text: Parakeet".
    note: str | None = None
    # More clips from the saved download (D-034): possible now, or why not.
    can_add_clips: bool = False
    add_clips_blocked: str | None = None
    # When the work now running for this video began (None when idle); drives the timer.
    active_since: datetime | None = None
    checkpoints: list[Checkpoint]
    clips: list[ClipSummary]


class Batch(BaseModel):
    id: str
    created_at: datetime
    status: str
    style: dict[str, Any]
    videos: list[Video]


class BatchSummary(BaseModel):
    id: str
    created_at: datetime
    last_activity_at: datetime
    status: str
    working: bool
    title: str
    video_count: int
    clip_count: int


class Metadata(BaseModel):
    title: str
    youtube_title: str
    youtube_description: str
    hashtags: list[str]
    linkedin_post: str
    reason: str


class EditState(BaseModel):
    caption_style: CaptionStyle
    caption_language: CaptionLanguage
    crop_mode: CropMode
    blur: int = Field(ge=0, le=60)
    start_s: float
    end_s: float
    # Manual blur boxes (D-048), clip seconds and shares of the 1080x1920 canvas.
    blur_boxes: list[BlurBox] = []
    # Montage (D-051): the planned source ranges ([] when the clip has none) and whether the
    # clip plays only them. Caption words and blur boxes are in the played clip's time.
    parts: list[tuple[float, float]] = []
    montage: bool = False


class WordOut(BaseModel):
    text: str
    start: float
    end: float


class SourceBounds(BaseModel):
    min_start_s: float
    max_end_s: float


class LiveBase(BaseModel):
    """The editor's live preview (D-057): `media/base` shows the clip's current layout
    without blur boxes, title and captions; the editor draws those itself at these canvas
    positions (1080x1920 pixels)."""

    title_y: int
    caption_y: int
    show_title: bool
    # Changes when a new base is rendered; the editor reloads the video with it.
    version: str


class Clip(ClipSummary):
    video_id: str
    batch_id: str
    metadata: Metadata
    edit: EditState
    words: list[WordOut]
    source_bounds: SourceBounds
    # Notes about the last render, e.g. "No face was found, so the crop is centered." (AA-047)
    warning: str | None = None
    length_warning: str | None = None
    # None until a preview render made a base for the clip's current trim, crop and blur.
    live: LiveBase | None = None


def error_of(code: str | None, message: str | None) -> ErrorBody | None:
    if not code:
        return None
    try:
        error_code = ErrorCode(code)
    except ValueError:
        error_code = ErrorCode.INTERNAL
    return ErrorBody(code=error_code, message=message or "")


def file_exists(ctx: JobContext, stored: str | None) -> bool:
    return bool(stored) and ctx.absolute(stored or "").is_file()


def clip_summary(ctx: JobContext, clip: ClipRow, positions: dict[str, int]) -> ClipSummary:
    return ClipSummary(
        id=clip.id,
        index=clip.idx,
        status=clip.status,
        stage=clip.stage,
        progress=clip.progress,
        queue_position=positions.get(clip.id) if clip.status == "queued" else None,
        title=clip.title or "",
        start_s=clip.start_s,
        end_s=clip.end_s,
        duration_s=round(total(played_parts(clip)), 2),
        thumbnail_url=f"/api/clips/{clip.id}/media/thumbnail"
        if file_exists(ctx, clip.thumbnail_path)
        else None,
        error=error_of(clip.error_code, clip.error_message) if clip.status == "failed" else None,
        edited=bool(clip.edited),
        has_preview=file_exists(ctx, clip.preview_path),
        has_export=file_exists(ctx, clip.export_path),
        updated_at=clip.updated_at,
    )


def video_view(
    ctx: JobContext,
    video: VideoRow,
    clips: list[ClipRow],
    positions: dict[str, int],
    active_since: datetime | None = None,
    paused: bool = False,
) -> Video:
    states = checkpoints(video, clips, ctx.video_dir(video.id), paused=paused)
    blocked = more_clips_blocker(video, ctx.video_dir(video.id))
    return Video(
        id=video.id,
        url=video.url,
        platform=video.platform,
        title=video.title,
        channel=video.channel,
        duration_s=video.duration_s,
        thumbnail_url=video.thumbnail_path,
        has_chat=bool(video.has_chat),
        status=video.status,
        stage=video.stage,
        progress=video.progress,
        error=error_of(video.error_code, video.error_message),
        note=video.note,
        can_add_clips=blocked is None,
        add_clips_blocked=blocked,
        active_since=active_since,
        checkpoints=[
            Checkpoint(key=s.key, label=s.label, state=s.state, detail=s.detail, engine=s.engine)  # type: ignore[arg-type]
            for s in states
        ],
        clips=[clip_summary(ctx, clip, positions) for clip in clips],
    )


def batch_view(ctx: JobContext, session: Session, batch: BatchRow) -> Batch:
    positions = repo.queued_clip_positions(session)
    videos = []
    for video in repo.videos_of(session, batch.id):
        clips = repo.clips_of_video(session, video.id)
        since = repo.generation_started_at(session, video.id, [clip.id for clip in clips])
        videos.append(
            video_view(ctx, video, clips, positions, since, paused=batch.status == "paused")
        )
    return Batch(
        id=batch.id,
        created_at=batch.created_at,
        status=batch.status,
        style=json.loads(batch.style_json),
        videos=videos,
    )


def source_bounds(ctx: JobContext, clip: ClipRow, video: VideoRow) -> SourceBounds:
    """Whole video while the source exists; else the saved cut with its handles (docs/08)."""
    video_dir = ctx.video_dir(video.id)
    if (video_dir / SOURCE_FILE_NAME).is_file() and video.duration_s:
        return SourceBounds(min_start_s=0.0, max_end_s=video.duration_s)
    cut = load_cut(ctx.clip_work_dir(clip.id))
    if cut is not None:
        return SourceBounds(min_start_s=cut.source_start, max_end_s=cut.source_end)
    if (video_dir / INFO_FILE_NAME).is_file() and video.duration_s:
        return SourceBounds(min_start_s=clip.start_s, max_end_s=clip.end_s)
    return SourceBounds(min_start_s=clip.start_s, max_end_s=clip.end_s)
