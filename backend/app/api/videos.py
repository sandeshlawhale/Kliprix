"""Video endpoints (docs/07-api-contract.md, "Videos")."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.config import MAX_URL_LENGTH, MAX_VIDEOS_PER_BATCH
from app.core.errors import ERROR_RESPONSES, AppError, ErrorBody, ErrorCode
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
