"""Settings endpoints (docs/07-api-contract.md, "Settings").

Stored as one row per top-level key; anything not stored falls back to the defaults
below (AA-012). PUT takes a partial body and merges it, `style_defaults` included.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.api.deps import DbDep
from app.core.errors import ERROR_RESPONSES, AppError, ErrorCode
from app.db.repositories import settings as settings_repo

router = APIRouter(prefix="/api", tags=["settings"], responses=ERROR_RESPONSES)

CaptionStyle = Literal["pop", "karaoke", "boxed"]
CaptionLanguage = Literal["en", "hinglish"]
ClipLengthMode = Literal["standard", "extended"]
CropMode = Literal["auto", "face", "full"]
Theme = Literal["light", "dark", "system"]
WhisperSetting = Literal["auto", "tiny", "base", "small", "medium"]
SourcePolicy = Literal["ask", "delete", "keep"]

MIN_CLIPS_PER_VIDEO = 1
MAX_CLIPS_PER_VIDEO = 5
MIN_BLUR = 0
MAX_BLUR = 60


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StyleOverrides(_Strict):
    caption_style: CaptionStyle
    caption_language: CaptionLanguage
    clip_length_mode: ClipLengthMode
    clips_per_video: int = Field(ge=MIN_CLIPS_PER_VIDEO, le=MAX_CLIPS_PER_VIDEO)
    blur: int = Field(ge=MIN_BLUR, le=MAX_BLUR)
    crop_mode: CropMode
    show_title: bool


class Settings(_Strict):
    theme: Theme
    style_defaults: StyleOverrides
    whisper_model: WhisperSetting
    source_policy: SourcePolicy
    storage_warn_gb: float = Field(gt=0)
    thorough_mode: bool


class StyleOverridesUpdate(_Strict):
    caption_style: CaptionStyle | None = None
    caption_language: CaptionLanguage | None = None
    clip_length_mode: ClipLengthMode | None = None
    clips_per_video: int | None = Field(
        default=None, ge=MIN_CLIPS_PER_VIDEO, le=MAX_CLIPS_PER_VIDEO
    )
    blur: int | None = Field(default=None, ge=MIN_BLUR, le=MAX_BLUR)
    crop_mode: CropMode | None = None
    show_title: bool | None = None


class SettingsUpdate(_Strict):
    """Any subset of Settings; `style_defaults` may itself be partial."""

    theme: Theme | None = None
    style_defaults: StyleOverridesUpdate | None = None
    whisper_model: WhisperSetting | None = None
    source_policy: SourcePolicy | None = None
    storage_warn_gb: float | None = Field(default=None, gt=0)
    thorough_mode: bool | None = None


# Defaults (AA-012): first caption style, English, standard length, 5 clips, the layout
# spec's blur radius 30, auto crop, title on; "ask" so nothing is deleted without the user;
# 10 GB storage warning (A-006).
DEFAULT_SETTINGS = Settings(
    theme="system",
    style_defaults=StyleOverrides(
        caption_style="pop",
        caption_language="en",
        clip_length_mode="standard",
        clips_per_video=MAX_CLIPS_PER_VIDEO,
        blur=30,
        crop_mode="auto",
        show_title=True,
    ),
    whisper_model="auto",
    source_policy="ask",
    storage_warn_gb=10.0,
    thorough_mode=False,
)


def _current(db: DbDep) -> dict[str, Any]:
    merged = DEFAULT_SETTINGS.model_dump()
    stored = settings_repo.load_all(db)
    for key, value in stored.items():
        if key == "style_defaults" and isinstance(value, dict):
            merged[key] = {**merged[key], **value}
        elif key in merged:
            merged[key] = value
    return merged


@router.get("/settings")
def get_settings(db: DbDep) -> Settings:
    """Current settings (stored values over defaults)."""
    return Settings.model_validate(_current(db))


@router.put("/settings")
def put_settings(update: SettingsUpdate, db: DbDep) -> Settings:
    """Merge a partial update into the stored settings and return the result.

    Errors: VALIDATION_ERROR if a value is out of range or explicitly null.
    """
    changes = update.model_dump(exclude_unset=True)
    merged = _current(db)
    for key, value in changes.items():
        if key == "style_defaults" and isinstance(value, dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    try:
        result = Settings.model_validate(merged)
    except ValidationError as exc:
        errors = [
            {"loc": [str(part) for part in err["loc"]], "msg": err["msg"]} for err in exc.errors()
        ]
        raise AppError(
            ErrorCode.VALIDATION_ERROR, "These settings are not valid.", {"errors": errors}
        ) from exc
    dumped = result.model_dump()
    settings_repo.save_many(db, {key: dumped[key] for key in changes})
    db.commit()
    return result
