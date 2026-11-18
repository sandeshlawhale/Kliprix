"""Batch endpoints (docs/07 "Batches"): create, history, detail, delete, cancel, SSE events."""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbDep
from app.api.views import Batch, BatchSummary, batch_view
from app.config import MAX_URL_LENGTH, MAX_VIDEOS_PER_BATCH
from app.core.errors import ERROR_RESPONSES, AppError, ErrorCode
from app.core.events import Event, EventBus
from app.core.settings_schema import StyleOverridesUpdate
from app.db.models import utc_now
from app.db.repositories import batches as repo
from app.db.repositories.settings import load_settings
from app.jobs.context import JobContext
from app.jobs.runner import JobRunner
from app.llm.session import LlmSessionStore
from app.sources.registry import resolve

router = APIRouter(prefix="/api/batches", tags=["batches"], responses=ERROR_RESPONSES)

DEFAULT_PAGE = 20
MAX_PAGE = 100
HEARTBEAT_S = 15.0


def get_ctx(request: Request) -> JobContext:
    ctx: JobContext = request.app.state.job_context
    return ctx


def get_runner(request: Request) -> JobRunner:
    runner: JobRunner = request.app.state.job_runner
    return runner


CtxDep = Annotated[JobContext, Depends(get_ctx)]
RunnerDep = Annotated[JobRunner, Depends(get_runner)]


class CreateBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urls: list[Annotated[str, Field(max_length=MAX_URL_LENGTH)]] = Field(min_length=1)
    style: StyleOverridesUpdate | None = None


class CreatedBatch(BaseModel):
    id: str


class BatchPage(BaseModel):
    items: list[BatchSummary]
    next_cursor: str | None


def _encode_cursor(created_at: datetime, batch_id: str) -> str:
    raw = f"{created_at.isoformat()}|{batch_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        created, batch_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(created), batch_id
    except (ValueError, UnicodeDecodeError) as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, "The cursor is not valid.") from exc


@router.post("", status_code=201)
def create_batch(
    body: CreateBatchRequest, request: Request, db: DbDep, ctx: CtxDep, runner: RunnerDep
) -> CreatedBatch:
    """Queue 1-3 links with the style snapshot (settings defaults + overrides).

    Errors: TOO_MANY_VIDEOS, INVALID_URL, UNSUPPORTED_HOST, LLM_NOT_CONFIGURED.
    """
    if len(body.urls) > MAX_VIDEOS_PER_BATCH:
        raise AppError(
            ErrorCode.TOO_MANY_VIDEOS,
            f"Up to {MAX_VIDEOS_PER_BATCH} videos at a time.",
            {"max": MAX_VIDEOS_PER_BATCH},
        )
    store: LlmSessionStore = request.app.state.llm_session
    if store.get() is None:
        raise AppError(
            ErrorCode.LLM_NOT_CONFIGURED, "Configure the AI model first (Configure API)."
        )
    urls = [resolve(url, ctx.adapters)[1] for url in body.urls]
    style: dict[str, Any] = load_settings(db).style_defaults.model_dump()
    if body.style is not None:
        style.update(body.style.model_dump(exclude_unset=True))
    batch = repo.create_batch(db, urls, style, title=None)
    db.commit()
    runner.wake()
    return CreatedBatch(id=batch.id)


@router.get("")
def list_batches(
    db: DbDep,
    ctx: CtxDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
) -> BatchPage:
    """History, newest first, with an opaque cursor."""
    before = _decode_cursor(cursor) if cursor else None
    rows = repo.list_batches(db, limit + 1, before)
    page, more = rows[:limit], len(rows) > limit
    counts = repo.batch_counts(db, [b.id for b in page])
    items = []
    for batch in page:
        videos = repo.videos_of(db, batch.id)
        title = (
            batch.title
            or next((v.title for v in videos if v.title), None)
            or (videos[0].url if videos else "Batch")
        )
        video_count, clip_count = counts[batch.id]
        items.append(
            BatchSummary(
                id=batch.id,
                created_at=batch.created_at,
                status=batch.status,
                title=title,
                video_count=video_count,
                clip_count=clip_count,
            )
        )
    next_cursor = _encode_cursor(page[-1].created_at, page[-1].id) if more and page else None
    return BatchPage(items=items, next_cursor=next_cursor)


def _require(db: DbDep, batch_id: str) -> Any:
    batch = repo.get_batch(db, batch_id)
    if batch is None:
        raise AppError(ErrorCode.NOT_FOUND, "This batch does not exist.")
    return batch


@router.get("/{batch_id}")
def get_batch(batch_id: str, db: DbDep, ctx: CtxDep) -> Batch:
    """The batch with its videos, checkpoints and clips."""
    return batch_view(ctx, db, _require(db, batch_id))


def _cancel(db: DbDep, runner: JobRunner, batch_id: str) -> None:
    videos = repo.videos_of(db, batch_id)
    clips = repo.clips_of_batch(db, batch_id)
    refs = [v.id for v in videos] + [c.id for c in clips]
    repo.cancel_jobs(db, refs)
    for video in videos:
        if video.status in ("queued", "running"):
            video.status = "cancelled"
    for clip in clips:
        if clip.status in ("queued", "running"):
            clip.status = "cancelled"
    batch = repo.get_batch(db, batch_id)
    if batch is not None:
        batch.status = "cancelled"
    db.commit()
    runner.cancel_refs(refs)


@router.post("/{batch_id}/cancel")
def cancel_batch(batch_id: str, db: DbDep, ctx: CtxDep, runner: RunnerDep) -> Batch:
    """Stop the running step, cancel queued work, and remove partial clip files."""
    batch = _require(db, batch_id)
    if batch.status in ("queued", "running"):
        _cancel(db, runner, batch_id)
        ctx.publish(batch_id, "batch", status="cancelled")
    return batch_view(ctx, db, _require(db, batch_id))


@router.delete("/{batch_id}", status_code=204)
def delete_batch(
    batch_id: str, db: DbDep, ctx: CtxDep, runner: RunnerDep, delete_files: bool = False
) -> Response:
    """Delete the batch from history; with `delete_files=true` also its files."""
    _require(db, batch_id)
    _cancel(db, runner, batch_id)
    videos = repo.videos_of(db, batch_id)
    clips = repo.clips_of_batch(db, batch_id)
    folders = [ctx.video_dir(v.id) for v in videos] + [ctx.clip_work_dir(c.id) for c in clips]
    files = [
        ctx.absolute(path)
        for c in clips
        for path in (c.preview_path, c.thumbnail_path, c.export_path)
        if path
    ]
    repo.delete_batch(db, batch_id)
    db.commit()
    if delete_files:
        for folder in [*folders, ctx.data_dir / "exports" / batch_id]:
            shutil.rmtree(folder, ignore_errors=True)
        for path in files:
            path.unlink(missing_ok=True)
    return Response(status_code=204)


def _sse(event: Event) -> str:
    return f"event: {event.kind}\ndata: {json.dumps(event.data)}\n\n"


@router.get("/{batch_id}/events", response_class=StreamingResponse)
async def batch_events(batch_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events: `video`, `clip`, `batch` hints and a heartbeat every 15 s."""
    bus: EventBus = request.app.state.event_bus

    async def stream() -> AsyncIterator[str]:
        queue = bus.subscribe(batch_id)
        try:
            yield f": connected {utc_now().isoformat()}\n\n"
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield _sse(event)
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
