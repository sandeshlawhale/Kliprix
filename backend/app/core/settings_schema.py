"""Settings schema and defaults (docs/07 "Settings", AA-012), shared by the API and the job
runner. Stored as one row per top-level key; missing keys fall back to the defaults."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CaptionStyle = Literal["pop", "karaoke", "boxed"]
CaptionLanguage = Literal["en", "hinglish"]
ClipLengthMode = Literal["standard", "extended"]
# "facecam" and "center" are the gaming layouts (D-052); Auto picks them for gaming videos.
CropMode = Literal["auto", "face", "full", "facecam", "center"]
Theme = Literal["light", "dark", "system"]
WhisperSetting = Literal["auto", "tiny", "base", "small", "medium"]
# Speech-to-text engine (D-028). Parakeet is English-only; Hinglish always uses Whisper.
TranscribeEngine = Literal["parakeet", "whisper"]
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
    transcribe_engine: TranscribeEngine
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
    transcribe_engine: TranscribeEngine | None = None
    source_policy: SourcePolicy | None = None
    storage_warn_gb: float | None = Field(default=None, gt=0)
    thorough_mode: bool | None = None


# Defaults (AA-012): first caption style, English, standard length, 5 clips, the layout
# spec's blur radius 30, auto crop, title on; "ask" so nothing is deleted without the user;
# 10 GB storage warning (A-006). Parakeet is the default engine on the owner's instruction
# (D-028); "whisper" switches back.
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
    transcribe_engine="parakeet",
    source_policy="ask",
    storage_warn_gb=10.0,
    thorough_mode=False,
)


def merge_stored(stored: dict[str, Any]) -> dict[str, Any]:
    """Defaults overlaid with stored values (`style_defaults` merged key by key)."""
    merged = DEFAULT_SETTINGS.model_dump()
    for key, value in stored.items():
        if key == "style_defaults" and isinstance(value, dict):
            merged[key] = {**merged[key], **value}
        elif key in merged:
            merged[key] = value
    return merged
