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


TransliterationAnswer: TypeAdapter[list[str]] = TypeAdapter(list[str])
