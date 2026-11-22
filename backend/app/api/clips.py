"""Clip endpoints (docs/07 "Clips"): detail, edit, preview/export/retry jobs, media, download."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.batches import CtxDep, RunnerDep
from app.api.deps import DbDep
from app.api.views import (
    Clip,
    EditState,
    Metadata,
    WordOut,
    clip_summary,
    file_exists,
    source_bounds,
)
from app.config import CLIP_EXTENDED_MAX_S, CLIP_MIN_S
from app.core.errors import ERROR_RESPONSES, AppError, ErrorCode
from app.core.settings_schema import CaptionLanguage, CaptionStyle, CropMode
from app.db.models import ClipRow
from app.db.repositories import batches as repo
from app.jobs.clip_jobs import clip_blur_boxes, clip_words
from app.jobs.context import JobContext
from app.pipeline.clip_render import CROP_INFO_FILE_NAME, CropInfo
from app.pipeline.select import TITLE_MAX_CHARS, all_words, words_between
from app.pipeline.transcribe import Word
from app.pipeline.transcribe import load_cached as load_transcript
from app.render.censor import MAX_BLUR_BOXES, BlurBox

router = APIRouter(prefix="/api/clips", tags=["clips"], responses=ERROR_RESPONSES)

EDIT_MIN_S = 5.0
TEXT_MAX = 5000
WORD_MAX = 100
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9 ._-]+")
VIDEO_FIELDS = ("caption_style", "caption_language", "crop_mode", "blur", "start_s", "end_s")
MEDIA_TYPES = {"preview": "video/mp4", "export": "video/mp4", "thumbnail": "image/jpeg"}


class WordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=WORD_MAX)
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class ClipPatch(BaseModel):
    """Any subset of metadata fields, edit fields, and words."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=TITLE_MAX_CHARS)
    youtube_title: str | None = Field(default=None, max_length=TEXT_MAX)
    youtube_description: str | None = Field(default=None, max_length=TEXT_MAX)
    hashtags: list[Annotated[str, Field(max_length=WORD_MAX)]] | None = None
    linkedin_post: str | None = Field(default=None, max_length=TEXT_MAX)
    reason: str | None = Field(default=None, max_length=TEXT_MAX)
    caption_style: CaptionStyle | None = None
    caption_language: CaptionLanguage | None = None
    crop_mode: CropMode | None = None
    blur: int | None = Field(default=None, ge=0, le=60)
    start_s: float | None = Field(default=None, ge=0)
    end_s: float | None = Field(default=None, ge=0)
    words: list[WordIn] | None = None
    blur_boxes: list[BlurBox] | None = Field(default=None, max_length=MAX_BLUR_BOXES)


class Accepted(BaseModel):
    queued: bool


def _require(db: DbDep, clip_id: str) -> ClipRow:
    clip = repo.get_clip(db, clip_id)
    if clip is None:
        raise AppError(ErrorCode.NOT_FOUND, "This clip does not exist.")
    return clip


def _crop_warning(ctx: JobContext, clip: ClipRow) -> str | None:
    path = ctx.clip_work_dir(clip.id) / CROP_INFO_FILE_NAME
    if not path.is_file():
        return None
    try:
        return CropInfo.model_validate_json(path.read_text(encoding="utf-8")).warning
    except ValueError:
        return None


def _length_warning(length: float) -> str | None:
    if length < CLIP_MIN_S or length > CLIP_EXTENDED_MAX_S:
        return f"This clip is {length:.0f} s long; 25-90 s works best for shorts."
    return None


def clip_view(ctx: JobContext, db: DbDep, clip: ClipRow) -> Clip:
    video = repo.get_video(db, clip.video_id)
    if video is None:
        raise AppError(ErrorCode.NOT_FOUND, "This clip's video does not exist.")
    summary = clip_summary(ctx, clip, repo.queued_clip_positions(db))
    return Clip(
        **summary.model_dump(),
        video_id=clip.video_id,
        batch_id=clip.batch_id,
        metadata=Metadata(
            title=clip.title or "",
            youtube_title=clip.youtube_title or "",
            youtube_description=clip.youtube_description or "",
            hashtags=json.loads(clip.hashtags_json or "[]"),
            linkedin_post=clip.linkedin_post or "",
            reason=clip.reason or "",
        ),
        edit=EditState(
            caption_style=clip.caption_style,  # type: ignore[arg-type]
            caption_language=clip.caption_language,  # type: ignore[arg-type]
            crop_mode=clip.crop_mode,  # type: ignore[arg-type]
            blur=clip.blur,
            start_s=clip.start_s,
            end_s=clip.end_s,
            blur_boxes=clip_blur_boxes(clip),
        ),
        words=[WordOut(**w.model_dump()) for w in clip_words(clip)],
        source_bounds=source_bounds(ctx, clip, video),
        warning=_crop_warning(ctx, clip)
        or (clip.error_message if clip.status != "failed" else None),
        length_warning=_length_warning(clip.end_s - clip.start_s),
    )


@router.get("/{clip_id}")
def get_clip(clip_id: str, db: DbDep, ctx: CtxDep) -> Clip:
    """The clip with metadata, edit state, words, and trim bounds."""
    return clip_view(ctx, db, _require(db, clip_id))


def _retimed_words(ctx: JobContext, clip: ClipRow, new_start: float, new_end: float) -> list[Word]:
    """Words for a new trim: the clip's (possibly edited) words that stay inside, shifted to
    the new start, plus transcript words from newly included parts."""
    old_start, old_end = clip.start_s, clip.end_s
    kept = []
    for word in clip_words(clip):
        absolute_start, absolute_end = word.start + old_start, word.end + old_start
        if absolute_end > new_start and absolute_start < new_end:
            kept.append((absolute_start, absolute_end, word.text))
    transcript = load_transcript(ctx.video_dir(clip.video_id))
    for word in words_between(all_words(transcript), new_start, new_end):
        if word.end <= old_start or word.start >= old_end:
            kept.append((word.start, word.end, word.text))
    kept.sort()
    return [
        Word(
            text=text,
            start=round(max(0.0, s - new_start), 2),
            end=round(max(0.0, e - new_start), 2),
        )
        for s, e, text in kept
    ]


@router.patch("/{clip_id}")
def patch_clip(clip_id: str, body: ClipPatch, db: DbDep, ctx: CtxDep) -> Clip:
    """Edit metadata, captions, style, blur, crop, or trim.

    Video-affecting changes (edit fields, words, and the on-video title) set `edited` and
    clear the preview and export. Errors: VALIDATION_ERROR (title > 40 characters, trim
    outside `source_bounds`, length outside 5-90 s), CONFLICT while the clip renders.
    """
    clip = _require(db, clip_id)
    changes: dict[str, Any] = body.model_dump(exclude_unset=True)
    if any(value is None for value in changes.values()):
        raise AppError(ErrorCode.VALIDATION_ERROR, "Fields cannot be set to null.")
    video_changed = any(key in changes for key in (*VIDEO_FIELDS, "words", "blur_boxes")) or (
        "title" in changes and changes["title"] != (clip.title or "")
    )
    if video_changed and clip.status == "running":
        raise AppError(ErrorCode.CONFLICT, "Wait until the current render finishes.")
    new_start = changes.get("start_s", clip.start_s)
    new_end = changes.get("end_s", clip.end_s)
    if "start_s" in changes or "end_s" in changes:
        video = repo.get_video(db, clip.video_id)
        if video is None:
            raise AppError(ErrorCode.NOT_FOUND, "This clip's video does not exist.")
        bounds = source_bounds(ctx, clip, video)
        if new_start < bounds.min_start_s - 0.01 or new_end > bounds.max_end_s + 0.01:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "The trim goes beyond the available video.",
                {"source_bounds": bounds.model_dump()},
            )
        length = new_end - new_start
        if length < EDIT_MIN_S or length > CLIP_EXTENDED_MAX_S:
            raise AppError(ErrorCode.VALIDATION_ERROR, "A clip must be 5-90 seconds long.")
        if "words" not in changes:
            clip.words_json = json.dumps(
                [w.model_dump() for w in _retimed_words(ctx, clip, new_start, new_end)]
            )
    if "words" in changes:
        length = new_end - new_start
        words = [Word(**w) for w in changes.pop("words")]
        if any(w.end < w.start or w.start > length + 0.01 for w in words):
            raise AppError(ErrorCode.VALIDATION_ERROR, "Word times must lie inside the clip.")
        clip.words_json = json.dumps([w.model_dump() for w in words])
    if "blur_boxes" in changes:
        length = new_end - new_start
        boxes = [BlurBox(**b) for b in changes.pop("blur_boxes")]
        if any(b.start > length + 0.01 for b in boxes):
            raise AppError(ErrorCode.VALIDATION_ERROR, "Blur boxes must start inside the clip.")
        clip.blur_boxes_json = json.dumps([b.model_dump() for b in boxes])
    if "hashtags" in changes:
        clip.hashtags_json = json.dumps(changes.pop("hashtags"))
    for key, value in changes.items():
        setattr(clip, key, value)
    if video_changed:
        clip.edited = 1
        for stored in (clip.preview_path, clip.export_path):
            if stored:
                ctx.absolute(stored).unlink(missing_ok=True)
        clip.preview_path = None
        clip.export_path = None
    db.commit()
    return clip_view(ctx, db, clip)


def _queue(db: DbDep, runner: RunnerDep, clip: ClipRow, kind: str) -> Accepted:
    if repo.active_job_for(db, clip.id, kind) is not None:
        return Accepted(queued=False)
    repo.add_job(db, kind, clip.id, repo.PRIORITY_USER)
    if clip.status in ("done", "failed", "cancelled"):
        clip.status = "queued"
        clip.error_code = None
        clip.error_message = None
    batch = repo.get_batch(db, clip.batch_id)
    if batch is not None and batch.status not in ("queued", "running"):
        batch.status = "running"
    db.commit()
    runner.wake()
    return Accepted(queued=True)


@router.post("/{clip_id}/preview", status_code=202)
def preview_clip(clip_id: str, db: DbDep, runner: RunnerDep) -> Accepted:
    """Queue a 540x960 preview render (ahead of background clips)."""
    return _queue(db, runner, _require(db, clip_id), "clip_preview")


@router.post("/{clip_id}/render", status_code=202)
def render_clip(clip_id: str, db: DbDep, runner: RunnerDep) -> Accepted:
    """Queue the full-quality export."""
    return _queue(db, runner, _require(db, clip_id), "clip_render")


@router.post("/{clip_id}/retry", status_code=202)
def retry_clip(clip_id: str, db: DbDep, runner: RunnerDep) -> Accepted:
    """Render a failed or cancelled clip again."""
    clip = _require(db, clip_id)
    if clip.status not in ("failed", "cancelled"):
        raise AppError(ErrorCode.CONFLICT, "Only a failed or cancelled clip can be retried.")
    return _queue(db, runner, clip, "clip_render")


@router.get("/{clip_id}/media/{kind}", response_class=FileResponse)
def clip_media(
    clip_id: str, kind: Literal["preview", "export", "thumbnail"], db: DbDep, ctx: CtxDep
) -> FileResponse:
    """The preview, export, or thumbnail (HTTP Range supported). Only DB-known files under
    DATA_DIR are served."""
    clip = _require(db, clip_id)
    stored = {"preview": clip.preview_path, "export": clip.export_path,
              "thumbnail": clip.thumbnail_path}[kind]  # fmt: skip
    if not file_exists(ctx, stored):
        raise AppError(ErrorCode.NOT_FOUND, f"There is no {kind} for this clip yet.")
    path = ctx.absolute(stored or "").resolve()
    if not path.is_relative_to(ctx.data_dir.resolve()):
        raise AppError(ErrorCode.NOT_FOUND, "Not found.")
    return FileResponse(path, media_type=MEDIA_TYPES[kind], content_disposition_type="inline")


def safe_filename(title: str) -> str:
    name = _UNSAFE_FILENAME.sub("", title).strip(" .") or "clip"
    return f"{name[:80]}.mp4"


@router.get("/{clip_id}/download", response_class=FileResponse)
def download_clip(clip_id: str, db: DbDep, ctx: CtxDep) -> Response:
    """The export as an attachment. Errors: CONFLICT when there is no current export."""
    clip = _require(db, clip_id)
    if not file_exists(ctx, clip.export_path):
        raise AppError(ErrorCode.CONFLICT, "Export the clip first.")
    path = ctx.absolute(clip.export_path or "").resolve()
    if not path.is_relative_to(ctx.data_dir.resolve()):
        raise AppError(ErrorCode.NOT_FOUND, "Not found.")
    # safe_filename keeps only [A-Za-z0-9 ._-], so the plain quoted form is valid (docs/07).
    disposition = f'attachment; filename="{safe_filename(clip.title or "")}"'
    return FileResponse(path, media_type="video/mp4", headers={"Content-Disposition": disposition})
