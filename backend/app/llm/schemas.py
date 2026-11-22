"""Schemas for LLM answers (docs/10). Lenient on purpose: fields the LLM adds are ignored and
lengths are repaired afterwards by the select stage, so a slightly long title does not
cost a retry."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class LlmClip(BaseModel):
    model_config = ConfigDict(extra="ignore")

    candidate_id: int | None = None
    start: float
    end: float
    needs_extended: bool = False
    # funny, informative, emotional, conflict, surprising, ... (D-035); shown in the reason.
    category: str = ""
    title: str = ""
    youtube_title: str = ""
    youtube_description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    linkedin_post: str = ""
    reason: str = ""
    score: float = 0.5


class SelectionAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    clips: list[LlmClip]


class TitleText(BaseModel):
    """The text for one clip from the title step (D-043)."""

    model_config = ConfigDict(extra="ignore")

    id: int
    title: str = ""
    youtube_title: str = ""
    youtube_description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    linkedin_post: str = ""
    reason: str = ""


class TitleAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    clips: list[TitleText]


class ReviewClip(BaseModel):
    """Round 2 (D-046): one draft kept (with fixed times and text) or dropped."""

    model_config = ConfigDict(extra="ignore")

    id: int
    keep: bool = True
    problem: str = ""
    start: float | None = None
    end: float | None = None
    category: str = ""
    title: str = ""
    youtube_title: str = ""
    youtube_description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    linkedin_post: str = ""
    reason: str = ""
    score: float = 0.5


class ReviewAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    clips: list[ReviewClip]


TransliterationAnswer: TypeAdapter[list[str]] = TypeAdapter(list[str])
