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
    LiveBase,
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
from app.jobs.clip_jobs import clip_blur_boxes, clip_words, played_parts, stored_parts
from app.jobs.context import JobContext
from app.pipeline.clip_render import (
    BASE_FILE_NAME,
    CROP_INFO_FILE_NAME,
    BaseInfo,
    CropInfo,
    load_base,
)
from app.pipeline.parts import (
    Part,
    effective,
    is_montage,
    normalized,
    to_source,
    total,
    words_for,
)
from app.pipeline.select import TITLE_MAX_CHARS, all_words, words_between
from app.pipeline.transcribe import Word
from app.pipeline.transcribe import load_cached as load_transcript
from app.render.censor import MAX_BLUR_BOXES, BlurBox

router = APIRouter(prefix="/api/clips", tags=["clips"], responses=ERROR_RESPONSES)

EDIT_MIN_S = 5.0
TEXT_MAX = 5000
WORD_MAX = 100
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9 ._-]+")
VIDEO_FIELDS = (
    "caption_style", "caption_language", "crop_mode", "blur", "start_s", "end_s", "montage",
)  # fmt: skip
MEDIA_TYPES = {
    "preview": "video/mp4", "export": "video/mp4", "base": "video/mp4", "thumbnail": "image/jpeg",
}  # fmt: skip
# Editor parts (D-059): at most this many, each at least this long.
MAX_PARTS = 20
MIN_PART_S = 0.5
# Two played ranges match when their edges are within this (stored times are rounded).
BASE_MATCH_S = 0.01


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
    # Montage on/off (D-051): play only the planned parts, or the whole range.
    montage: bool | None = None
    # The source ranges to play (D-059: split, delete and extend in the editor). Replaces
    # start_s/end_s/montage: one range = a plain trim, several = a montage of them.
    parts: list[tuple[float, float]] | None = Field(
        default=None, min_length=1, max_length=MAX_PARTS
    )


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


def current_base(ctx: JobContext, clip: ClipRow) -> BaseInfo | None:
    """The live-preview base (D-057) when it still shows this clip: rendered for the same
    played ranges, crop choice and blur. Words, title, style and blur boxes do not matter,
    the editor draws those."""
    base = load_base(ctx.clip_work_dir(clip.id))
    if base is None or base.crop != clip.crop_mode or base.blur != clip.blur:
        return None
    played = played_parts(clip)
    if len(played) != len(base.parts):
        return None
    for (a, b), (c, d) in zip(played, base.parts, strict=True):
        if abs(a - c) > BASE_MATCH_S or abs(b - d) > BASE_MATCH_S:
            return None
    return base


def _live(ctx: JobContext, clip: ClipRow, base: BaseInfo | None) -> LiveBase | None:
    if base is None:
        return None
    try:
        version = str((ctx.clip_work_dir(clip.id) / BASE_FILE_NAME).stat().st_mtime_ns)
    except OSError:
        return None
    return LiveBase(
        title_y=base.title_y, caption_y=base.caption_y, show_title=base.show_title,
        version=version,
    )  # fmt: skip


def _requested_parts(raw: list[tuple[float, float]]) -> tuple[Part, ...]:
    """The editor's parts (D-059), sorted and merged where they touch. Errors:
    VALIDATION_ERROR for a part that ends before it starts or is shorter than 0.5 s."""
    if any(end <= start or start < 0 for start, end in raw):
        raise AppError(ErrorCode.VALIDATION_ERROR, "Each part must end after it starts.")
    parts = normalized(raw)
    if not parts or any(end - start < MIN_PART_S - 0.01 for start, end in parts):
        raise AppError(ErrorCode.VALIDATION_ERROR, "Each part must be at least 0.5 s long.")
    return tuple((round(a, 2), round(b, 2)) for a, b in parts)


def _length_warning(length: float) -> str | None:
    if length < CLIP_MIN_S or length > CLIP_EXTENDED_MAX_S:
        return f"This clip is {length:.0f} s long; 25-90 s works best for shorts."
    return None


def clip_view(ctx: JobContext, db: DbDep, clip: ClipRow) -> Clip:
    video = repo.get_video(db, clip.video_id)
    if video is None:
        raise AppError(ErrorCode.NOT_FOUND, "This clip's video does not exist.")
    summary = clip_summary(ctx, clip, repo.queued_clip_positions(db))
    base = current_base(ctx, clip)
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
            parts=list(stored_parts(clip)),
            montage=bool(clip.montage) and bool(stored_parts(clip)),
        ),
        words=[WordOut(**w.model_dump()) for w in clip_words(clip)],
        source_bounds=source_bounds(ctx, clip, video),
        warning=_crop_warning(ctx, clip)
        or (clip.error_message if clip.status != "failed" else None),
        length_warning=_length_warning(total(played_parts(clip))),
        live=_live(ctx, clip, base),
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


def _montage_words(
    ctx: JobContext,
    clip: ClipRow,
    old: tuple[Part, ...],
    new: tuple[Part, ...],
    edited: list[Word] | None = None,
    edited_parts: tuple[Part, ...] | None = None,
) -> list[Word]:
    """Words for a montage change (D-051): the clip's (possibly edited) words mapped back to
    source time, plus transcript words of newly played ranges, in the new clip's time.
    `edited` are words sent with the change, timed over `edited_parts` (D-062: the editor
    sends them in the new clip's time)."""
    timing = edited_parts if edited_parts is not None else old
    kept = [
        Word(
            text=w.text,
            start=to_source(w.start, timing),
            end=to_source(w.start, timing) + w.end - w.start,
        )
        for w in (edited if edited is not None else clip_words(clip))
    ]
    transcript = load_transcript(ctx.video_dir(clip.video_id))
    for word in all_words(transcript):
        middle = (word.start + word.end) / 2
        played = any(a <= middle < b for a, b in new)
        if played and not any(a <= middle < b for a, b in old):
            kept.append(word)
    kept.sort(key=lambda w: w.start)
    return words_for(kept, new)


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
    video_changed = any(
        key in changes for key in (*VIDEO_FIELDS, "words", "blur_boxes", "parts")
    ) or ("title" in changes and changes["title"] != (clip.title or ""))
    if video_changed and clip.status == "running":
        raise AppError(ErrorCode.CONFLICT, "Wait until the current render finishes.")
    old_parts = played_parts(clip)
    if "parts" in changes:
        new_parts = _requested_parts(changes.pop("parts"))
        changes.pop("montage", None)
        new_start, new_end = new_parts[0][0], new_parts[-1][1]
        changes["start_s"], changes["end_s"] = new_start, new_end
        if is_montage(new_parts):
            clip.parts_json = json.dumps([list(part) for part in new_parts])
            clip.montage = 1
        else:
            # A single range keeps the planned parts, so the montage switch can bring them back.
            clip.montage = 0
    else:
        new_start = changes.get("start_s", clip.start_s)
        new_end = changes.get("end_s", clip.end_s)
        planned = stored_parts(clip)
        if changes.get("montage") and not planned:
            raise AppError(ErrorCode.VALIDATION_ERROR, "This clip has no montage parts.")
        montage_on = changes.get("montage", bool(clip.montage)) and bool(planned)
        new_parts = (
            effective(new_start, new_end, planned) if montage_on else ((new_start, new_end),)
        )
    played = total(new_parts)
    if "start_s" in changes or "end_s" in changes or "montage" in changes:
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
        if played < EDIT_MIN_S or played > CLIP_EXTENDED_MAX_S:
            raise AppError(ErrorCode.VALIDATION_ERROR, "A clip must be 5-90 seconds long.")
        if "words" in changes:
            # Words sent with a cut are in the new clip's time (D-062); transcript words of
            # newly played ranges (an extend) are added.
            sent = [Word(**w) for w in changes.pop("words")]
            words = _montage_words(
                ctx, clip, old_parts, new_parts, edited=sent, edited_parts=new_parts
            )
        elif is_montage(old_parts) or is_montage(new_parts):
            words = _montage_words(ctx, clip, old_parts, new_parts)
        else:
            words = _retimed_words(ctx, clip, new_start, new_end)
        clip.words_json = json.dumps([w.model_dump() for w in words])
    if "words" in changes:
        words = [Word(**w) for w in changes.pop("words")]
        if any(w.end < w.start or w.start > played + 0.01 for w in words):
            raise AppError(ErrorCode.VALIDATION_ERROR, "Word times must lie inside the clip.")
        clip.words_json = json.dumps([w.model_dump() for w in words])
    if "blur_boxes" in changes:
        boxes = [BlurBox(**b) for b in changes.pop("blur_boxes")]
        if any(b.start > played + 0.01 for b in boxes):
            raise AppError(ErrorCode.VALIDATION_ERROR, "Blur boxes must start inside the clip.")
        clip.blur_boxes_json = json.dumps([b.model_dump() for b in boxes])
    if "hashtags" in changes:
        clip.hashtags_json = json.dumps(changes.pop("hashtags"))
    if "montage" in changes:
        clip.montage = 1 if changes.pop("montage") else 0
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
    clip_id: str,
    kind: Literal["preview", "export", "base", "thumbnail"],
    db: DbDep,
    ctx: CtxDep,
) -> FileResponse:
    """The preview, export, live-preview base (D-057), or thumbnail (HTTP Range supported).
    Only DB-known files under DATA_DIR, and a base that still matches the clip, are served."""
    clip = _require(db, clip_id)
    if kind == "base":
        if current_base(ctx, clip) is None:
            raise AppError(ErrorCode.NOT_FOUND, "Make a preview to edit this clip live.")
        base_path = (ctx.clip_work_dir(clip.id) / BASE_FILE_NAME).resolve()
        return FileResponse(base_path, media_type="video/mp4", content_disposition_type="inline")
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
