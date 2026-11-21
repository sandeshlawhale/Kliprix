"""Storage endpoints (docs/07 "Storage", docs/08 "Retention"): usage per folder and
user-triggered cleanups. Nothing is deleted without a request from the user."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.api.batches import CtxDep
from app.api.deps import DbDep
from app.config import BYTES_PER_GB
from app.core.errors import ERROR_RESPONSES, AppError, ErrorCode
from app.db.models import BatchRow, ClipRow, VideoRow
from app.db.repositories import batches as repo
from app.db.repositories.settings import load_settings
from app.jobs.context import JobContext
from app.pipeline.audio import AUDIO_FILE_NAME
from app.sources.base import SOURCE_FILE_NAME

router = APIRouter(prefix="/api/storage", tags=["storage"], responses=ERROR_RESPONSES)

FOLDERS = ("downloads", "exports", "previews", "work", "models")
ACTIVE = ("queued", "running")


class FolderSizes(BaseModel):
    downloads: int
    exports: int
    previews: int
    work: int
    models: int


class StorageInfo(BaseModel):
    total_bytes: int
    folders: FolderSizes
    warn: bool


class CleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["source_videos", "previews", "work", "batch", "all"]
    batch_id: str | None = None


class CleanupResult(BaseModel):
    freed_bytes: int


def folder_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


@router.get("")
def get_storage(db: DbDep, ctx: CtxDep) -> StorageInfo:
    """Bytes used per data folder, and whether the total is over the warning size."""
    sizes = {name: folder_size(ctx.data_dir / name) for name in FOLDERS}
    total = sum(sizes.values())
    warn = total > load_settings(db).storage_warn_gb * BYTES_PER_GB
    return StorageInfo(total_bytes=total, folders=FolderSizes(**sizes), warn=warn)


def _remove_file(path: Path) -> int:
    try:
        size = path.stat().st_size
        path.unlink()
    except FileNotFoundError:
        return 0
    return size


def _remove_tree(path: Path) -> int:
    size = folder_size(path)
    shutil.rmtree(path, ignore_errors=True)
    return size if not path.exists() else 0


def _video_idle(db: DbDep, video: VideoRow) -> bool:
    clips = repo.clips_of_video(db, video.id)
    return video.status not in ACTIVE and not any(clip.status in ACTIVE for clip in clips)


def _clean_sources(db: DbDep, ctx: JobContext, videos: list[VideoRow]) -> int:
    """Source video and audio of finished videos (signals, transcript, cuts stay)."""
    freed = 0
    for video in videos:
        if _video_idle(db, video):
            folder = ctx.video_dir(video.id)
            freed += _remove_file(folder / SOURCE_FILE_NAME) + _remove_file(
                folder / AUDIO_FILE_NAME
            )
    return freed


def _clean_previews(db: DbDep, ctx: JobContext, clips: list[ClipRow]) -> int:
    freed = 0
    for clip in clips:
        if clip.status not in ACTIVE and clip.preview_path:
            freed += _remove_file(ctx.absolute(clip.preview_path))
            clip.preview_path = None
    return freed


def _clean_work(ctx: JobContext, clips: list[ClipRow]) -> int:
    return sum(
        _remove_tree(ctx.clip_work_dir(clip.id)) for clip in clips if clip.status not in ACTIVE
    )


def _clear_all(db: DbDep, ctx: JobContext) -> int:
    """Everything Kliprix stored: batch history, downloads, exports, previews, thumbnails,
    work files and downloaded models. Settings and logs stay. Refused while work runs."""
    if any(v.status in ACTIVE for v in db.query(VideoRow)) or any(
        c.status in ACTIVE for c in db.query(ClipRow)
    ):
        raise AppError(
            ErrorCode.VALIDATION_ERROR, "Wait for running or queued work to finish, or cancel it."
        )
    freed = 0
    for batch in list(db.query(BatchRow)):
        repo.delete_batch(db, batch.id)
    for name in (*FOLDERS, "thumbnails"):
        folder = ctx.data_dir / name
        freed += folder_size(folder)
        for child in folder.iterdir() if folder.is_dir() else ():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    return freed


@router.post("/cleanup")
def cleanup(body: CleanupRequest, db: DbDep, ctx: CtxDep) -> CleanupResult:
    """Free space. `source_videos`: downloaded videos of finished batches; `previews`;
    `work` (cut/crop files; trimming beyond the clip then needs the source); `batch`: all
    three for one batch. `source_videos` with `batch_id` limits it to that batch (the "Ask"
    retention dialog). Running work is never touched.

    Errors: VALIDATION_ERROR (batch scope without batch_id), NOT_FOUND.
    """
    if body.batch_id and repo.get_batch(db, body.batch_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "This batch does not exist.")
    if body.scope == "all":
        freed = _clear_all(db, ctx)
    elif body.scope == "source_videos" and body.batch_id:
        freed = _clean_sources(db, ctx, repo.videos_of(db, body.batch_id))
    elif body.scope == "batch":
        if not body.batch_id:
            raise AppError(ErrorCode.VALIDATION_ERROR, "Choose the batch to clean up.")
        videos = repo.videos_of(db, body.batch_id)
        clips = repo.clips_of_batch(db, body.batch_id)
        freed = (
            _clean_sources(db, ctx, videos)
            + _clean_previews(db, ctx, clips)
            + _clean_work(ctx, clips)
        )
    else:
        all_videos = list(db.query(VideoRow))
        all_clips = list(db.query(ClipRow))
        if body.scope == "source_videos":
            freed = _clean_sources(db, ctx, all_videos)
        elif body.scope == "previews":
            freed = _clean_previews(db, ctx, all_clips)
        else:
            freed = _clean_work(ctx, all_clips)
    db.commit()
    return CleanupResult(freed_bytes=freed)
