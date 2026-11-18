"""Stage 8, select: one LLM call per video picks up to N clips with metadata, then the backend
validates and repairs them and fills gaps from the remaining candidates (docs/04, 10).

Repairs: times inside the video and the candidate region, length 25-90 s (target 30-60,
up to 90 only with `needs_extended` in extended mode), no overlap and a 30 s gap between
clips, no duplicates, text lengths, emoji-free titles, and hashtag format.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import (
    CLIP_EXTENDED_MAX_S,
    CLIP_MIN_GAP_S,
    CLIP_MIN_S,
    CLIP_REGION_MARGIN_S,
    CLIP_STANDARD_MAX_S,
    CLIP_TARGET_MIN_S,
    LLM_SELECT_TEMPERATURE,
)
from app.core.files import write_json_atomic
from app.llm.client import LlmClient
from app.llm.prompts import PromptCandidate, clock, select_system, select_user
from app.llm.schemas import LlmClip, SelectionAnswer
from app.pipeline.candidates import Candidate
from app.pipeline.transcribe import TranscriptFile, Word

logger = logging.getLogger(__name__)

SELECTION_FILE_NAME = "selection.json"
TITLE_MAX_CHARS = 40
TITLE_MAX_WORDS = 7
YOUTUBE_TITLE_MAX_CHARS = 100
DESCRIPTION_MAX_CHARS = 300
LINKEDIN_MAX_CHARS = 600
REASON_MAX_CHARS = 300
HASHTAGS_MIN = 3
HASHTAGS_MAX = 6
# Added (in order) when the LLM gives fewer than HASHTAGS_MIN valid hashtags.
DEFAULT_HASHTAGS = ("#shorts", "#highlights", "#clips")
# Quote characters stripped from the ends of titles: ASCII, curly, and guillemets.
_QUOTES = "\"'`" + "".join(map(chr, (0x201C, 0x201D, 0x2018, 0x2019, 0xAB, 0xBB)))
_HASHTAG_BODY = re.compile(r"[^\w]", re.UNICODE)
TIME_DECIMALS = 2
# Zero-width joiner and emoji variation selector, left over when emoji are removed.
_JOINERS = (chr(0x200D), chr(0xFE0F))


class SelectionClip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    title: str
    youtube_title: str
    youtube_description: str
    hashtags: list[str]
    linkedin_post: str
    reason: str
    score: float = Field(ge=0, le=1)
    needs_extended: bool = False


class SelectionFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    clips: list[SelectionClip]


@dataclass(frozen=True)
class VideoFacts:
    title: str
    channel: str | None
    duration_s: float


def load_cached(video_dir: Path) -> SelectionFile | None:
    path = video_dir / SELECTION_FILE_NAME
    if not path.is_file():
        return None
    try:
        return SelectionFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


# ---------- text repair ----------


def truncate_words(text: str, max_chars: int, max_words: int | None = None) -> str:
    """Cut at a word boundary so the result fits; never mid-word unless one word is too long."""
    words = text.split()
    if max_words is not None:
        words = words[:max_words]
    result = " ".join(words)
    if len(result) <= max_chars:
        return result
    cut = result[: max_chars + 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else result[:max_chars]).rstrip(" ,;:-")


def strip_emoji(text: str) -> str:
    """Remove emoji and other pictographic symbols (Unicode category So/Cs) and joiners."""
    kept = [
        char
        for char in text
        if unicodedata.category(char) not in ("So", "Cs", "Sk") and char not in _JOINERS
    ]
    return " ".join("".join(kept).split())


def clean_title(text: str) -> str:
    title = strip_emoji(text).strip().strip(_QUOTES).strip()
    return truncate_words(title, TITLE_MAX_CHARS, TITLE_MAX_WORDS)


def clean_hashtags(tags: Sequence[str]) -> list[str]:
    result: list[str] = []
    for tag in tags:
        body = _HASHTAG_BODY.sub("", tag.strip().lstrip("#"))
        if not body:
            continue
        hashtag = f"#{body}"
        if hashtag.lower() not in {existing.lower() for existing in result}:
            result.append(hashtag)
    result = result[:HASHTAGS_MAX]
    for default in DEFAULT_HASHTAGS:
        if len(result) >= HASHTAGS_MIN:
            break
        if default not in result:
            result.append(default)
    return result


# ---------- time repair ----------


def _word_at(t: float, words: Sequence[Word]) -> Word | None:
    for word in words:
        if word.start < t < word.end:
            return word
    return None


def snap_start(t: float, words: Sequence[Word]) -> float:
    """Move a start that falls inside a word back to that word's start."""
    word = _word_at(t, words)
    return word.start if word is not None else t


def snap_end(t: float, words: Sequence[Word]) -> float:
    """Move an end that falls inside a word forward to that word's end."""
    word = _word_at(t, words)
    return word.end if word is not None else t


def snap_end_before(t: float, words: Sequence[Word]) -> float:
    """Move an end that falls inside a word back to before that word (used when trimming)."""
    word = _word_at(t, words)
    return word.start if word is not None else t


def fit_window(
    start: float, end: float, duration_s: float, max_len: float, words: Sequence[Word]
) -> tuple[float, float] | None:
    """Clamp to the video, snap to word edges, and bring the length into
    [CLIP_MIN_S, max_len] (short clips grow to CLIP_TARGET_MIN_S). None if impossible."""
    start = max(0.0, min(start, duration_s))
    end = max(0.0, min(end, duration_s))
    if end <= start:
        return None
    start = snap_start(start, words)
    end = min(duration_s, snap_end(end, words))
    if end - start > max_len:
        end = snap_end_before(start + max_len, words)
    if end - start < CLIP_MIN_S:
        missing = CLIP_TARGET_MIN_S - (end - start)
        end = min(duration_s, end + missing)
        start = max(0.0, end - CLIP_TARGET_MIN_S)
    length = end - start
    if length < CLIP_MIN_S or length > max_len + 1e-6:
        return None
    return round(start, TIME_DECIMALS), round(end, TIME_DECIMALS)


def conflicts(start: float, end: float, accepted: Sequence[SelectionClip]) -> bool:
    """True when the window overlaps an accepted clip or comes within the minimum gap."""
    return any(
        start < clip.end + CLIP_MIN_GAP_S and end > clip.start - CLIP_MIN_GAP_S for clip in accepted
    )


# ---------- building clips ----------


def all_words(transcript: TranscriptFile | None) -> list[Word]:
    if transcript is None:
        return []
    return [word for segment in transcript.segments for word in segment.words]


def words_between(words: Sequence[Word], start: float, end: float) -> list[Word]:
    return [word for word in words if word.end > start and word.start < end]


def _signals_text(candidate: Candidate) -> str:
    parts = [
        f"{name} {value:.2f}"
        for name, value in candidate.signals.model_dump().items()
        if value is not None
    ]
    return ", ".join(parts)


def repair_clip(
    raw: LlmClip,
    candidates: Sequence[Candidate],
    facts: VideoFacts,
    words: Sequence[Word],
    extended: bool,
) -> SelectionClip | None:
    """A valid clip from the LLM's answer, or None when its times cannot be repaired."""
    start, end = raw.start, raw.end
    candidate_id = raw.candidate_id
    if candidate_id is not None and 0 <= candidate_id < len(candidates):
        candidate = candidates[candidate_id]
        low = max(0.0, candidate.start - CLIP_REGION_MARGIN_S)
        high = min(facts.duration_s, candidate.end + CLIP_REGION_MARGIN_S)
        start, end = max(start, low), min(end, high)
    allow_long = extended and raw.needs_extended
    max_len = CLIP_EXTENDED_MAX_S if allow_long else CLIP_STANDARD_MAX_S
    window = fit_window(start, end, facts.duration_s, max_len, words)
    if window is None:
        return None
    start, end = window
    title = clean_title(raw.title) or fallback_title(words_between(words, start, end), start)
    return SelectionClip(
        start=start,
        end=end,
        title=title,
        youtube_title=truncate_words(raw.youtube_title.strip() or title, YOUTUBE_TITLE_MAX_CHARS),
        youtube_description=truncate_words(raw.youtube_description.strip(), DESCRIPTION_MAX_CHARS),
        hashtags=clean_hashtags(raw.hashtags),
        linkedin_post=truncate_words(raw.linkedin_post.strip(), LINKEDIN_MAX_CHARS),
        reason=truncate_words(raw.reason.strip(), REASON_MAX_CHARS),
        score=min(1.0, max(0.0, raw.score)),
        needs_extended=end - start > CLIP_STANDARD_MAX_S,
    )


def fallback_title(words: Sequence[Word], start: float) -> str:
    """The first words spoken in the clip, or "Highlight at mm:ss" when nobody speaks."""
    text = clean_title(" ".join(word.text for word in words[: TITLE_MAX_WORDS * 2]))
    return text or f"Highlight at {clock(start)}"


def fallback_clip(
    candidate: Candidate, facts: VideoFacts, words: Sequence[Word]
) -> SelectionClip | None:
    """A clip made from a candidate window without the LLM (docs/04: fill from candidates)."""
    window = fit_window(
        candidate.start, candidate.end, facts.duration_s, CLIP_STANDARD_MAX_S, words
    )
    if window is None:
        return None
    start, end = window
    spoken = words_between(words, start, end)
    title = fallback_title(spoken, start)
    snippet = truncate_words(" ".join(word.text for word in spoken), DESCRIPTION_MAX_CHARS)
    description = snippet or truncate_words(
        f"A highlight from {facts.title} at {clock(start)}.", DESCRIPTION_MAX_CHARS
    )
    return SelectionClip(
        start=start,
        end=end,
        title=title,
        youtube_title=truncate_words(title, YOUTUBE_TITLE_MAX_CHARS),
        youtube_description=description,
        hashtags=list(DEFAULT_HASHTAGS),
        linkedin_post=truncate_words(
            f"A moment from {facts.title} ({clock(start)}).", LINKEDIN_MAX_CHARS
        ),
        reason=f"Picked from signal peaks ({_signals_text(candidate)}).",
        score=candidate.score,
    )


def prompt_candidates(
    candidates: Sequence[Candidate], transcript: TranscriptFile | None
) -> list[PromptCandidate]:
    """Candidates with their transcript lines in [start - 30, end + 30] for the prompt."""
    segments = transcript.segments if transcript is not None else []
    result = []
    for index, candidate in enumerate(candidates):
        low = candidate.start - CLIP_REGION_MARGIN_S
        high = candidate.end + CLIP_REGION_MARGIN_S
        lines = [(s.start, s.text) for s in segments if s.end > low and s.start < high and s.text]
        signals = {
            name: value
            for name, value in candidate.signals.model_dump().items()
            if value is not None
        }
        result.append(
            PromptCandidate(index, candidate.start, candidate.end, candidate.score, signals, lines)
        )
    return result


def build_selection(
    answer: SelectionAnswer,
    candidates: Sequence[Candidate],
    facts: VideoFacts,
    transcript: TranscriptFile | None,
    count: int,
    extended: bool,
) -> SelectionFile:
    """Validate and repair the LLM's clips, then fill up to `count` from the candidates."""
    words = all_words(transcript)
    accepted: list[SelectionClip] = []
    for raw in sorted(answer.clips, key=lambda clip: -clip.score):
        if len(accepted) >= count:
            break
        clip = repair_clip(raw, candidates, facts, words, extended)
        if clip is None:
            logger.info(
                "llm clip dropped: times cannot be repaired (%.1f-%.1f)", raw.start, raw.end
            )
            continue
        if conflicts(clip.start, clip.end, accepted):
            logger.info("llm clip dropped: overlaps another clip (%.1f-%.1f)", clip.start, clip.end)
            continue
        accepted.append(clip)
    from_llm = len(accepted)
    for candidate in candidates:
        if len(accepted) >= count:
            break
        clip = fallback_clip(candidate, facts, words)
        if clip is not None and not conflicts(clip.start, clip.end, accepted):
            accepted.append(clip)
    logger.info("selection clips=%s from_llm=%s fallback=%s", len(accepted), from_llm,
                len(accepted) - from_llm)  # fmt: skip
    return SelectionFile(clips=accepted)


def run_select(
    video_dir: Path,
    facts: VideoFacts,
    candidates: Sequence[Candidate],
    transcript: TranscriptFile | None,
    *,
    count: int,
    extended: bool,
    client: LlmClient,
) -> SelectionFile:
    """Write `selection.json` (skipped when a valid copy exists).

    Raises:
        AppError: LLM_AUTH_FAILED, LLM_RATE_LIMITED, or LLM_BAD_RESPONSE from the call.
    """
    cached = load_cached(video_dir)
    if cached is not None:
        return cached
    system = select_system(count, extended)
    user = select_user(
        facts.title, facts.channel, facts.duration_s, count,
        prompt_candidates(candidates, transcript),
    )  # fmt: skip
    answer = client.complete_json(system, user, SelectionAnswer, LLM_SELECT_TEMPERATURE)
    selection = build_selection(answer, candidates, facts, transcript, count, extended)
    write_json_atomic(video_dir / SELECTION_FILE_NAME, selection)
    return selection
