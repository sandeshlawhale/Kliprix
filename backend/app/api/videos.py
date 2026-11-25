"""Video endpoints (docs/07-api-contract.md, "Videos")."""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.batches import CtxDep, RunnerDep
from app.api.deps import DbDep
from app.api.views import Batch, batch_view
from app.config import MAX_URL_LENGTH, MAX_VIDEOS_PER_BATCH
from app.core.errors import ERROR_RESPONSES, AppError, ErrorBody, ErrorCode
from app.core.settings_schema import MAX_CLIPS_PER_VIDEO, MIN_CLIPS_PER_VIDEO
from app.db.repositories import batches as repo
from app.jobs.state import more_clips_blocker
from app.jobs.video_pipeline import PROGRESS_AT
from app.llm.session import LlmSessionStore
from app.sources.base import SOURCE_FILE_NAME, SourceAdapter, VideoMeta
from app.sources.inspect_cache import InspectCache
from app.sources.registry import resolve

logger = logging.getLogger(__name__)
# A custom clip (D-061): the same length rule as an edited clip.
CUSTOM_MIN_S = 5.0
CUSTOM_MAX_S = 90.0

router = APIRouter(prefix="/api/videos", tags=["videos"], responses=ERROR_RESPONSES)


def get_adapters(request: Request) -> list[SourceAdapter]:
    adapters: list[SourceAdapter] = request.app.state.source_adapters
    return adapters


AdaptersDep = Annotated[list[SourceAdapter], Depends(get_adapters)]


class InspectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urls: list[Annotated[str, Field(max_length=MAX_URL_LENGTH)]] = Field(min_length=1)


class InspectResult(BaseModel):
    """One link: `video` when `ok`, else `error` with a documented code."""

    url: str
    ok: bool
    video: VideoMeta | None = None
    error: ErrorBody | None = None


class InspectResponse(BaseModel):
    results: list[InspectResult]


@router.post("/inspect")
def inspect_videos(
    body: InspectRequest, request: Request, adapters: AdaptersDep
) -> InspectResponse:
    """Metadata for 1-3 links without downloading. Each link succeeds or fails on its own.

    Errors: TOO_MANY_VIDEOS for more than 3 links. Per link: INVALID_URL,
    UNSUPPORTED_HOST, VIDEO_*, GEO_BLOCKED, DOWNLOAD_FAILED, DOWNLOADER_OUTDATED.
    """
    if len(body.urls) > MAX_VIDEOS_PER_BATCH:
        raise AppError(
            ErrorCode.TOO_MANY_VIDEOS,
            f"Up to {MAX_VIDEOS_PER_BATCH} videos at a time.",
            {"max": MAX_VIDEOS_PER_BATCH},
        )
    results: list[InspectResult] = []
    for raw in body.urls:
        try:
            adapter, url = resolve(raw, adapters)
            video = adapter.inspect(url)
        except AppError as exc:
            error = ErrorBody(code=exc.code, message=exc.message, details=exc.details)
            results.append(InspectResult(url=raw, ok=False, error=error))
            continue
        cache: InspectCache | None = getattr(request.app.state, "inspect_cache", None)
        if cache is not None:
            cache.put(url, video)
        results.append(InspectResult(url=raw, ok=True, video=video))
    return InspectResponse(results=results)


class MoreClipsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=MIN_CLIPS_PER_VIDEO, le=MAX_CLIPS_PER_VIDEO)


class CustomClipRequest(BaseModel):
    """A clip range picked by hand on the batch page (D-061), source seconds."""

    model_config = ConfigDict(extra="forbid")

    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)


@router.post("/{video_id}/custom-clip", status_code=202)
def custom_clip(
    video_id: str,
    body: CustomClipRequest,
    request: Request,
    db: DbDep,
    ctx: CtxDep,
    runner: RunnerDep,
) -> Batch:
    """Queue one clip for a range the user picked from the saved download (D-061): the range
    is transcribed where needed, the AI model writes its title and metadata, and it renders
    like the other clips. Returns the batch.

    Errors: NOT_FOUND, LLM_NOT_CONFIGURED, CONFLICT (the video is still being processed, or
    its download was deleted), VALIDATION_ERROR (length outside 5-90 s or beyond the video).
    """
    video = repo.get_video(db, video_id)
    batch = repo.get_batch(db, video.batch_id) if video is not None else None
    if video is None or batch is None:
        raise AppError(ErrorCode.NOT_FOUND, "This video does not exist.")
    length = body.end_s - body.start_s
    if length < CUSTOM_MIN_S or length > CUSTOM_MAX_S:
        raise AppError(ErrorCode.VALIDATION_ERROR, "A clip must be 5-90 seconds long.")
    if video.duration_s and body.end_s > video.duration_s + 0.5:
        raise AppError(ErrorCode.VALIDATION_ERROR, "The range goes past the end of the video.")
    store: LlmSessionStore = request.app.state.llm_session
    if store.get() is None:
        raise AppError(
            ErrorCode.LLM_NOT_CONFIGURED, "Configure the AI model first (Configure API)."
        )
    blocked = more_clips_blocker(video, ctx.video_dir(video_id))
    if blocked is None and repo.active_video_job(db, video_id) is not None:
        blocked = "Wait until this video has finished."
    if blocked is not None:
        raise AppError(ErrorCode.CONFLICT, blocked)
    video.status = "queued"
    video.stage = "transcribe"
    video.error_code = None
    video.error_message = None
    batch.status = "queued"
    repo.touch_batch(batch)
    repo.add_job(
        db, "video_custom_clip", video_id, repo.PRIORITY_USER,
        {"start_s": round(body.start_s, 2), "end_s": round(body.end_s, 2)},
    )  # fmt: skip
    db.commit()
    runner.wake()
    ctx.publish(video.batch_id, "video", id=video.id, status=video.status, stage=video.stage,
                progress=video.progress)  # fmt: skip
    ctx.publish(video.batch_id, "batch", status="queued")
    return batch_view(ctx, db, batch)


@router.get("/{video_id}/source", response_class=FileResponse)
def video_source(video_id: str, db: DbDep, ctx: CtxDep) -> FileResponse:
    """The saved download (HTTP Range supported), for picking a custom clip's range (D-061).
    Errors: NOT_FOUND when the video or its download does not exist."""
    video = repo.get_video(db, video_id)
    if video is None:
        raise AppError(ErrorCode.NOT_FOUND, "This video does not exist.")
    path = (ctx.video_dir(video_id) / SOURCE_FILE_NAME).resolve()
    if not path.is_file() or not path.is_relative_to(ctx.data_dir.resolve()):
        raise AppError(ErrorCode.NOT_FOUND, "The downloaded video was deleted.")
    return FileResponse(path, media_type="video/mp4", content_disposition_type="inline")


@router.post("/{video_id}/more-clips", status_code=202)
def more_clips(
    video_id: str,
    body: MoreClipsRequest,
    request: Request,
    db: DbDep,
    ctx: CtxDep,
    runner: RunnerDep,
) -> Batch:
    """Queue up to `count` more clips from the video's saved download (D-034): no new
    download or signals; only untranscribed parts of new candidates are transcribed; new
    clips never overlap the existing ones. Returns the batch.

    Errors: NOT_FOUND, LLM_NOT_CONFIGURED, CONFLICT (the video is still being processed, or
    its download was deleted), VALIDATION_ERROR (count outside 1-5).
    """
    video = repo.get_video(db, video_id)
    batch = repo.get_batch(db, video.batch_id) if video is not None else None
    if video is None or batch is None:
        raise AppError(ErrorCode.NOT_FOUND, "This video does not exist.")
    store: LlmSessionStore = request.app.state.llm_session
    if store.get() is None:
        raise AppError(
            ErrorCode.LLM_NOT_CONFIGURED, "Configure the AI model first (Configure API)."
        )
    blocked = more_clips_blocker(video, ctx.video_dir(video_id))
    if blocked is None and repo.active_video_job(db, video_id) is not None:
        blocked = "Wait until this video has finished."
    if blocked is not None:
        raise AppError(ErrorCode.CONFLICT, blocked)
    video.status = "queued"
    video.stage = "candidates"
    video.progress = PROGRESS_AT["motion"]
    video.error_code = None
    video.error_message = None
    batch.status = "queued"
    repo.touch_batch(batch)
    repo.add_job(db, "video_more_clips", video_id, repo.PRIORITY_USER, {"count": body.count})
    db.commit()
    runner.wake()
    ctx.publish(video.batch_id, "video", id=video.id, status=video.status, stage=video.stage,
                progress=video.progress)  # fmt: skip
    ctx.publish(video.batch_id, "batch", status="queued")
    return batch_view(ctx, db, batch)


@router.post("/{video_id}/retry", status_code=202)
def retry_video(
    video_id: str,
    request: Request,
    db: DbDep,
    ctx: CtxDep,
    runner: RunnerDep,
) -> Batch:
    """Resume a failed video from the step that failed (P-001, approved 2026-11-24).

    Queues the video's last job again (the pipeline, or "more clips" with the same count).
    Every finished step is reused from its saved file (download, chat, signals,
    candidates, transcript, selection) and clips are never created twice, so the work
    continues where it stopped. Returns the batch.

    Errors: NOT_FOUND, LLM_NOT_CONFIGURED, CONFLICT (the video did not fail, or is queued
    or running).
    """
    video = repo.get_video(db, video_id)
    batch = repo.get_batch(db, video.batch_id) if video is not None else None
    if video is None or batch is None:
        raise AppError(ErrorCode.NOT_FOUND, "This video does not exist.")
    if video.status != "failed" or repo.active_video_job(db, video_id) is not None:
        raise AppError(ErrorCode.CONFLICT, "Only a failed video can be resumed.")
    store: LlmSessionStore = request.app.state.llm_session
    if store.get() is None:
        raise AppError(
            ErrorCode.LLM_NOT_CONFIGURED, "Configure the AI model first (Configure API)."
        )
    last = repo.last_video_job(db, video_id)
    kind = last.kind if last is not None else "video_pipeline"
    payload = json.loads(last.payload_json) if last is not None and last.payload_json else None
    video.status = "queued"
    video.error_code = None
    video.error_message = None
    batch.status = "queued"
    repo.touch_batch(batch)
    repo.add_job(db, kind, video_id, repo.PRIORITY_USER, payload)
    db.commit()
    logger.info("video resumed id=%s kind=%s stage=%s", video_id, kind, video.stage)
    runner.wake()
    ctx.publish(video.batch_id, "video", id=video.id, status=video.status, stage=video.stage,
                progress=video.progress)  # fmt: skip
    ctx.publish(video.batch_id, "batch", status="queued")
    return batch_view(ctx, db, batch)
