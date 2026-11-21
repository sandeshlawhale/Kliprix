"""Video endpoints (docs/07-api-contract.md, "Videos")."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
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
from app.sources.base import SourceAdapter, VideoMeta
from app.sources.registry import resolve

logger = logging.getLogger(__name__)

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
def inspect_videos(body: InspectRequest, adapters: AdaptersDep) -> InspectResponse:
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
        results.append(InspectResult(url=raw, ok=True, video=video))
    return InspectResponse(results=results)


class MoreClipsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=MIN_CLIPS_PER_VIDEO, le=MAX_CLIPS_PER_VIDEO)


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
    repo.add_job(db, "video_more_clips", video_id, repo.PRIORITY_USER, {"count": body.count})
    db.commit()
    runner.wake()
    ctx.publish(video.batch_id, "video", id=video.id, status=video.status, stage=video.stage,
                progress=video.progress)  # fmt: skip
    ctx.publish(video.batch_id, "batch", status="queued")
    return batch_view(ctx, db, batch)
