"""Schemas for LLM answers (docs/10). Lenient on purpose: fields the LLM adds are ignored and
lengths are repaired afterwards by the select stage, so a slightly long title does not
cost a retry."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


class LlmClip(BaseModel):
    model_config = ConfigDict(extra="ignore")

    candidate_id: int | None = None
    start: float
    end: float
    needs_extended: bool = False
    # funny, informative, emotional, conflict, surprising, ... (D-035); shown in the reason.
    category: str = ""
    # The kind of moment for the context rules (D-050): clutch, kill, funny, chaos, death, ...
    moment_type: str = ""
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
    # Context ids (D-050): utterances "U12" and events "E3" from the draft's timeline. The
    # backend turns them into times; start/end are only used when there are none.
    moment_type: str = ""
    setup: str | None = None
    core: list[str] = Field(default_factory=list)
    payoff: str | None = None
    end_after: str | None = None
    multi_round: bool = False
    category: str = ""
    title: str = ""
    youtube_title: str = ""
    youtube_description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    linkedin_post: str = ""
    reason: str = ""
    score: float = 0.5

    @field_validator("core", mode="before")
    @classmethod
    def _one_or_many(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
        return value

    @field_validator("setup", "payoff", "end_after", mode="before")
    @classmethod
    def _first_id(cls, value: Any) -> Any:
        if isinstance(value, list):
            return str(value[0]) if value else None
        return value


class ReviewAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    clips: list[ReviewClip]


class CritiqueRepair(BaseModel):
    """One fix the critic asks for (D-050): move an edge to an utterance or event."""

    model_config = ConfigDict(extra="ignore")

    action: str
    ref: str


class CritiqueClip(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    valid: bool = True
    worth_sharing: bool = True
    issues: list[str] = Field(default_factory=list)
    repairs: list[CritiqueRepair] = Field(default_factory=list)


class CritiqueAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    clips: list[CritiqueClip]


TransliterationAnswer: TypeAdapter[list[str]] = TypeAdapter(list[str])
