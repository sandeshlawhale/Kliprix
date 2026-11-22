"""Stage 8, select: one LLM call per video picks up to N clips with metadata, then the backend
validates and repairs them and fills gaps from the remaining candidates (docs/04, 10).

Repairs: times inside the video and the candidate region, whole sentences (D-033), length
25-90 s (target 30-60, up to 90 only with `needs_extended` in extended mode), no overlap and
a 30 s gap between clips, no duplicates, text lengths, emoji-free titles, and hashtag format.
Before the LLM call, candidates are re-ranked by how much is said in them (D-033).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import (
    CLIP_CONTEXT_BEFORE_S,
    CLIP_EXTENDED_MAX_S,
    CLIP_LEAD_PAD_S,
    CLIP_MIN_GAP_S,
    CLIP_MIN_S,
    CLIP_REGION_MARGIN_S,
    CLIP_STANDARD_MAX_S,
    CLIP_TAIL_PAD_S,
    CLIP_TARGET_MIN_S,
    CONTEXT_FIX_REACH_S,
    CONTEXT_FIX_SENTENCES,
    DANGLING_STARTS,
    LLM_SELECT_TEMPERATURE,
    REVIEW_DRAFT_FACTOR,
    REVIEW_DRAFT_MAX,
    REVIEW_DULL_MIN,
    REVIEW_MIN_S,
    REVIEW_STEP_S,
    REVIEW_WORKERS,
    SCREEN_FALLBACK_MAX_DULL,
    SENTENCE_SNAP_S,
    SPEECH_FALLBACK_MIN_WPS,
    SPEECH_FLOOR,
    SPEECH_FULL_WPS,
    SPEECH_VIDEO_MIN_WPS,
)
from app.core.errors import AppError, ErrorCode
from app.core.files import write_json_atomic
from app.llm.client import LlmClient
from app.llm.prompts import (
    PromptCandidate,
    TitleClip,
    clock,
    review_system,
    review_user,
    select_system,
    select_user,
    title_system,
    title_user,
)
from app.llm.schemas import (
    LlmClip,
    ReviewAnswer,
    ReviewClip,
    SelectionAnswer,
    TitleAnswer,
)
from app.pipeline.candidates import Candidate
from app.pipeline.review import ClipAnalysis
from app.pipeline.review import describe as describe_analysis
from app.pipeline.screen import ScreenFile, describe, dull_share, window_shares
from app.pipeline.screen_text import ScreenText, texts_between
from app.pipeline.sentences import (
    first_between,
    last_between,
    nearest,
    sentence_edges,
    words_per_second,
)
from app.pipeline.signals import message_groups
from app.pipeline.sound import SoundEvents, timeline
from app.pipeline.transcribe import TranscriptFile, Word
from app.sources.base import ChatMessage

logger = logging.getLogger(__name__)

SELECTION_FILE_NAME = "selection.json"
# (start, end) seconds of a clip already made.
Window = tuple[float, float]
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
# Rule fixes of a draft (D-046): action later than AUTO_LATE_S after the start moves the
# start to AUTO_LEAD_S before it; action within AUTO_REACH_S after the end extends the
# end to AUTO_TAIL_S after that burst (actions at most AUTO_GAP_S apart).
AUTO_LATE_S = 8.0
AUTO_LEAD_S = 5.0
AUTO_REACH_S = 10.0
AUTO_GAP_S = 6.0
AUTO_TAIL_S = 3.0
# Moving the start to late action never leaves less than this (a real 60 s talk-over clip
# was cut to 13 s otherwise).
AUTO_KEEP_S = 25.0
# Chat shown to the LLM (D-043): messages from 3 s before to 8 s after the reaction (or the
# peak), at most 10, reactions first.
CHAT_SAMPLE_BEFORE_S = 3.0
CHAT_SAMPLE_AFTER_S = 8.0
CHAT_SAMPLE_COUNT = 10
# Zero-width joiner and emoji variation selector, left over when emoji are removed.
_JOINERS = (chr(0x200D), chr(0xFE0F))
# Stripped from a word before it is compared with DANGLING_STARTS.
_WORD_EDGES = ".,!?;:\"'()[]…-—"


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
    # False for a clip filled from a candidate without the LLM; its text then comes from the
    # title step (D-043).
    from_llm: bool = True


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
    """Clamp to the video, snap to whole sentences (D-033; word edges where no sentence edge
    is near), and bring the length into [CLIP_MIN_S, max_len]: a long clip ends at the last
    sentence that fits, a short one grows to the first sentence end past CLIP_TARGET_MIN_S
    (or to CLIP_TARGET_MIN_S when there is none). Then a little room around the words.
    None if impossible."""
    start = max(0.0, min(start, duration_s))
    end = max(0.0, min(end, duration_s))
    if end <= start:
        return None
    edges = sentence_edges(words)
    snapped_start = nearest(edges.starts, start, SENTENCE_SNAP_S)
    snapped_end = nearest(edges.ends, end, SENTENCE_SNAP_S)
    start = snapped_start if snapped_start is not None else snap_start(start, words)
    end = min(duration_s, snapped_end if snapped_end is not None else snap_end(end, words))
    if end - start > max_len:
        fitting = last_between(edges.ends, start + CLIP_TARGET_MIN_S, start + max_len)
        end = fitting if fitting is not None else snap_end_before(start + max_len, words)
    if end - start < CLIP_MIN_S:
        longer = first_between(
            edges.ends, start + CLIP_TARGET_MIN_S, min(duration_s, start + max_len)
        )
        if longer is not None:
            end = longer
        else:
            end = min(duration_s, start + CLIP_TARGET_MIN_S)
            start = max(0.0, end - CLIP_TARGET_MIN_S)
    start, end = pad_window(start, end, duration_s, max_len, words)
    length = end - start
    if length < CLIP_MIN_S or length > max_len + 1e-6:
        return None
    return round(start, TIME_DECIMALS), round(end, TIME_DECIMALS)


def pad_window(
    start: float, end: float, duration_s: float, max_len: float, words: Sequence[Word]
) -> tuple[float, float]:
    """Widen by CLIP_LEAD_PAD_S / CLIP_TAIL_PAD_S so the first and last words are not
    clipped, never into a neighbouring word, past the video, or beyond `max_len`. Without
    words there is nothing to protect, so the window stays as it is."""
    if not words:
        return start, end
    before = max((w.end for w in words if w.end <= start), default=0.0)
    after = min((w.start for w in words if w.start >= end), default=duration_s)
    padded_start = max(0.0, before, start - CLIP_LEAD_PAD_S)
    padded_end = min(duration_s, after, end + CLIP_TAIL_PAD_S)
    if padded_end - padded_start > max_len:
        return start, end
    return padded_start, padded_end


def _first_word(words: Sequence[Word], at: float) -> str:
    following = [word for word in words if word.start >= at - 1e-6]
    if not following:
        return ""
    first = min(following, key=lambda word: word.start)
    return first.text.strip().lower().strip(_WORD_EDGES)


def with_context(
    start: float, end: float, max_len: float, words: Sequence[Word]
) -> tuple[float, float]:
    """Move the start back to the setup when the clip opens mid-thought (D-044): while the
    first word is a continuation ("so", "and", "exactly", "toh", ...), start at the previous
    sentence if it begins within CONTEXT_FIX_REACH_S and the clip stays within `max_len`;
    at most CONTEXT_FIX_SENTENCES times. The end never moves."""
    if not words:
        return start, end
    starts = sentence_edges(words).starts
    for _ in range(CONTEXT_FIX_SENTENCES):
        if _first_word(words, start) not in DANGLING_STARTS:
            break
        earlier = last_between(starts, start - CONTEXT_FIX_REACH_S, start - 0.01)
        if earlier is None or end - earlier > max_len:
            break
        start = earlier
    return round(start, TIME_DECIMALS), end


def conflicts(
    start: float, end: float, accepted: Sequence[SelectionClip], taken: Sequence[Window] = ()
) -> bool:
    """True when the window overlaps an accepted clip or an already made one (`taken`), or
    comes within the minimum gap of it."""
    windows = [*((clip.start, clip.end) for clip in accepted), *taken]
    return any(
        start < high + CLIP_MIN_GAP_S and end > low - CLIP_MIN_GAP_S for low, high in windows
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
        low = max(0.0, candidate.start - CLIP_CONTEXT_BEFORE_S)
        high = min(facts.duration_s, candidate.end + CLIP_REGION_MARGIN_S)
        start, end = max(start, low), min(end, high)
    allow_long = extended and raw.needs_extended
    max_len = CLIP_EXTENDED_MAX_S if allow_long else CLIP_STANDARD_MAX_S
    window = fit_window(start, end, facts.duration_s, max_len, words)
    if window is None:
        return None
    start, end = with_context(window[0], window[1], max_len, words)
    title = clean_title(raw.title) or fallback_title(start)
    return SelectionClip(
        start=start,
        end=end,
        title=title,
        youtube_title=truncate_words(raw.youtube_title.strip() or title, YOUTUBE_TITLE_MAX_CHARS),
        youtube_description=truncate_words(raw.youtube_description.strip(), DESCRIPTION_MAX_CHARS),
        hashtags=clean_hashtags(raw.hashtags),
        linkedin_post=truncate_words(raw.linkedin_post.strip(), LINKEDIN_MAX_CHARS),
        reason=truncate_words(_reason_text(raw), REASON_MAX_CHARS),
        score=min(1.0, max(0.0, raw.score)),
        needs_extended=end - start > CLIP_STANDARD_MAX_S,
    )


def _reason_text(raw: LlmClip) -> str:
    """The LLM's reason, led by its category when it gave one: "Funny: ..." (D-035)."""
    reason = raw.reason.strip()
    category = strip_emoji(raw.category).strip().strip(".:").capitalize()[:20]
    return f"{category}: {reason}" if category and reason else reason or category


def fallback_title(start: float) -> str:
    """An honest plain title for a clip without LLM text (D-043: never the transcript's first
    words, which read like a random caption)."""
    return f"Highlight at {clock(start)}"


def rank_by_speech(candidates: Sequence[Candidate], words: Sequence[Word]) -> list[Candidate]:
    """Weigh each candidate by how much is said in its window (D-033) and sort best first.

    The factor runs from SPEECH_FLOOR (silence) to 1 (SPEECH_FULL_WPS words per second or
    more) and is recorded as the `speech` signal. Videos where the windows together hold
    little speech (music, gameplay without commentary) keep their order and scores.
    """
    if not words or not candidates:
        return list(candidates)
    rates = [words_per_second(words, c.start, c.end) for c in candidates]
    seconds = sum(c.end - c.start for c in candidates)
    overall = sum(r * (c.end - c.start) for r, c in zip(rates, candidates, strict=True))
    if seconds <= 0 or overall / seconds < SPEECH_VIDEO_MIN_WPS:
        return list(candidates)
    ranked: list[Candidate] = []
    for candidate, rate in zip(candidates, rates, strict=True):
        speech = min(1.0, rate / SPEECH_FULL_WPS)
        factor = SPEECH_FLOOR + (1.0 - SPEECH_FLOOR) * speech
        ranked.append(
            candidate.model_copy(
                update={
                    "score": round(candidate.score * factor, 3),
                    "signals": candidate.signals.model_copy(update={"speech": round(speech, 3)}),
                }
            )
        )
    return sorted(ranked, key=lambda c: -c.score)


def fallback_clip(
    candidate: Candidate, facts: VideoFacts, words: Sequence[Word]
) -> SelectionClip | None:
    """A clip made from a candidate window without the LLM (docs/04: fill from candidates).
    None for a window with too little speech in a video that has speech (D-033): fewer
    clips beat a clip of a waiting screen."""
    speech = candidate.signals.speech
    if speech is not None and speech * SPEECH_FULL_WPS < SPEECH_FALLBACK_MIN_WPS:
        logger.info("fallback skipped: little speech at %.0f-%.0f", candidate.start, candidate.end)
        return None
    if candidate.screen is not None and dull_share(candidate.screen) >= SCREEN_FALLBACK_MAX_DULL:
        logger.info("fallback skipped: menu screen at %.0f-%.0f", candidate.start, candidate.end)
        return None
    window = fit_window(
        candidate.start, candidate.end, facts.duration_s, CLIP_STANDARD_MAX_S, words
    )
    if window is None:
        return None
    start, end = with_context(window[0], window[1], CLIP_STANDARD_MAX_S, words)
    spoken = words_between(words, start, end)
    title = fallback_title(start)
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
        from_llm=False,
    )


def prompt_candidates(
    candidates: Sequence[Candidate],
    transcript: TranscriptFile | None,
    evidence: Evidence | None = None,
) -> list[PromptCandidate]:
    """Candidates with their transcript lines in [start - 90, end + 30] (D-044) and what the
    other stages found there (D-043) for the prompt."""
    segments = transcript.segments if transcript is not None else []
    seen = evidence or Evidence()
    result = []
    for index, candidate in enumerate(candidates):
        low = candidate.start - CLIP_CONTEXT_BEFORE_S
        high = candidate.end + CLIP_REGION_MARGIN_S
        lines = [(s.start, s.text) for s in segments if s.end > low and s.start < high and s.text]
        signals = {
            name: value
            for name, value in candidate.signals.model_dump().items()
            if value is not None
        }
        facts = moment_facts(seen, candidate.start, candidate.end, candidate.screen)
        facts += chat_facts(seen, candidate)
        chat = chat_sample(seen, candidate.reaction_t or candidate.peak_t)
        result.append(
            PromptCandidate(
                index, candidate.start, candidate.end, candidate.score, signals, lines,
                facts, chat,
            )
        )  # fmt: skip
    return result


@dataclass(frozen=True)
class Evidence:
    """What the other stages found in the whole video (D-043): screen type, sound events and
    the chat replay. Every part is optional."""

    screen: ScreenFile | None = None
    events: SoundEvents | None = None
    chat: Sequence[ChatMessage] = ()
    # Text read on screen around the candidates (D-045).
    text: ScreenText | None = None


def moment_facts(
    evidence: Evidence, start: float, end: float, shares: dict[str, float] | None = None
) -> list[str]:
    """ "On screen: ..." and "Sounds: ..." lines for [start, end) (D-043)."""
    facts: list[str] = []
    if shares is None and evidence.screen is not None:
        shares = window_shares(evidence.screen, start, end) or None
    if shares:
        facts.append(f"On screen: {describe(shares)}")
    if evidence.events is not None:
        moments = timeline(evidence.events, start, end)
        if moments:
            facts.append("Sounds: " + "; ".join(f"{clock(t)} {text}" for t, text in moments))
    if evidence.text is not None:
        texts = texts_between(evidence.text, start, end)
        if texts:
            facts.append("On-screen text: " + ", ".join(f'"{text}"' for text in texts))
    return facts


def _group_counts(messages: Sequence[ChatMessage]) -> str:
    counts: dict[str, int] = {}
    for message in messages:
        for group in message_groups(message.text):
            counts[group] = counts.get(group, 0) + 1
    return ", ".join(f"{group} {n}" for group, n in sorted(counts.items(), key=lambda p: -p[1]))


def chat_facts(evidence: Evidence, candidate: Candidate) -> list[str]:
    """How chat reacted in the candidate (D-042, D-043)."""
    if not evidence.chat:
        return []
    inside = [m for m in evidence.chat if candidate.start <= m.t < candidate.end]
    if candidate.reaction_t is None:
        if not inside:
            return []
        groups = _group_counts(inside)
        return [f"Chat: {len(inside)} messages here" + (f" ({groups})" if groups else "")]
    near = [
        m
        for m in evidence.chat
        if candidate.reaction_t - CHAT_SAMPLE_BEFORE_S
        <= m.t
        < candidate.reaction_t + CHAT_SAMPLE_AFTER_S
    ]
    groups = _group_counts(near)
    line = f"Chat reacts at {clock(candidate.reaction_t)}" + (f" ({groups})" if groups else "")
    if candidate.cause_t is not None:
        line += f"; the likely cause is at {clock(candidate.cause_t)}, so start before it"
    return [line]


def chat_sample(evidence: Evidence, at: float) -> list[str]:
    """Up to CHAT_SAMPLE_COUNT distinct chat messages around `at`, reactions first."""
    near = [m for m in evidence.chat if at - CHAT_SAMPLE_BEFORE_S <= m.t < at + CHAT_SAMPLE_AFTER_S]
    ordered = sorted(near, key=lambda m: (not message_groups(m.text), m.t))
    sample: list[str] = []
    seen: set[str] = set()
    for message in ordered:
        key = message.text.strip().lower()
        if key and key not in seen:
            seen.add(key)
            sample.append(message.text.strip())
        if len(sample) >= CHAT_SAMPLE_COUNT:
            break
    return sample


def video_facts(evidence: Evidence) -> list[str]:
    """One line on what the whole video mostly shows (D-043), e.g. "Mostly on screen:
    gameplay 78%, people talking 15%"."""
    if evidence.screen is None or not evidence.screen.shares:
        return []
    shares = window_shares(evidence.screen, 0, len(evidence.screen.shares) * evidence.screen.step_s)
    text = describe(shares)
    return [f"Mostly on screen: {text}"] if text else []


def write_titles(
    clips: Sequence[SelectionClip],
    facts: VideoFacts,
    transcript: TranscriptFile | None,
    evidence: Evidence,
    client: LlmClient,
) -> list[SelectionClip]:
    """The title step (D-043): clips filled without the LLM get their text from one LLM call
    with their transcript and evidence. Clips that already have LLM text are returned as they
    are. If the call fails, the fallback text stays (an honest "Highlight at mm:ss" title)
    and the failure is logged: the clips are still good, only their text is plain."""
    todo = [index for index, clip in enumerate(clips) if not clip.from_llm]
    if not todo:
        return list(clips)
    segments = transcript.segments if transcript is not None else []
    items = []
    for index in todo:
        clip = clips[index]
        lines = [(s.start, s.text) for s in segments if s.end > clip.start and s.start < clip.end]
        chat = chat_sample(evidence, (clip.start + clip.end) / 2)
        items.append(
            TitleClip(
                index,
                clip.start,
                clip.end,
                lines,
                moment_facts(evidence, clip.start, clip.end),
                chat,
            )
        )
    try:
        answer = client.complete_json(
            title_system(), title_user(facts.title, facts.channel, items), TitleAnswer,
            LLM_SELECT_TEMPERATURE,
        )  # fmt: skip
    except AppError as exc:
        logger.warning("title step failed: %s", exc.code)
        return list(clips)
    result = list(clips)
    for text in answer.clips:
        if text.id not in todo:
            continue
        clip = result[text.id]
        title = clean_title(text.title)
        if not title:
            continue
        result[text.id] = clip.model_copy(
            update={
                "title": title,
                "youtube_title": truncate_words(
                    text.youtube_title.strip() or title, YOUTUBE_TITLE_MAX_CHARS
                ),
                "youtube_description": truncate_words(
                    text.youtube_description.strip() or clip.youtube_description,
                    DESCRIPTION_MAX_CHARS,
                ),
                "hashtags": clean_hashtags(text.hashtags),
                "linkedin_post": truncate_words(
                    text.linkedin_post.strip() or clip.linkedin_post, LINKEDIN_MAX_CHARS
                ),
                "reason": truncate_words(text.reason.strip() or clip.reason, REASON_MAX_CHARS),
                "from_llm": True,
            }
        )
    logger.info(
        "title step clips=%s written=%s",
        len(todo),
        sum(c.from_llm for c in result) - (len(clips) - len(todo)),
    )
    return result


def build_selection(
    answer: SelectionAnswer,
    candidates: Sequence[Candidate],
    facts: VideoFacts,
    transcript: TranscriptFile | None,
    count: int,
    extended: bool,
    taken: Sequence[Window] = (),
) -> SelectionFile:
    """Validate and repair the LLM's clips, then fill up to `count` from the candidates.
    Nothing may overlap or come within the gap of a `taken` window (clips already made)."""
    words = all_words(transcript)
    accepted: list[SelectionClip] = []
    # Most important first (D-035): the LLM's own score, then the order it listed them in.
    ranked = sorted(enumerate(answer.clips), key=lambda pair: (-pair[1].score, pair[0]))
    for _, raw in ranked:
        if len(accepted) >= count:
            break
        clip = repair_clip(raw, candidates, facts, words, extended)
        if clip is None:
            logger.info(
                "llm clip dropped: times cannot be repaired (%.1f-%.1f)", raw.start, raw.end
            )
            continue
        if conflicts(clip.start, clip.end, accepted, taken):
            logger.info("llm clip dropped: overlaps another clip (%.1f-%.1f)", clip.start, clip.end)
            continue
        accepted.append(clip)
    from_llm = len(accepted)
    for candidate in candidates:
        if len(accepted) >= count:
            break
        clip = fallback_clip(candidate, facts, words)
        if clip is not None and not conflicts(clip.start, clip.end, accepted, taken):
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
    evidence: Evidence | None = None,
    reviewer: Reviewer | None = None,
) -> SelectionFile:
    """Write `selection.json` (skipped when a valid copy exists). With a `reviewer` (D-046)
    round 1 drafts more clips and round 2 keeps, fixes or drops them after a close look.

    Raises:
        AppError: LLM_AUTH_FAILED, LLM_RATE_LIMITED, or LLM_BAD_RESPONSE from the call.
    """
    cached = load_cached(video_dir)
    if cached is not None:
        return cached
    # Candidate ids in the prompt and in the answer refer to this re-ranked list.
    candidates = rank_by_speech(candidates, all_words(transcript))
    seen = evidence or Evidence()
    drafts = draft_count(count, reviewer)
    system = select_system(drafts, extended)
    user = select_user(
        facts.title, facts.channel, facts.duration_s, drafts,
        prompt_candidates(candidates, transcript, seen), video_facts=video_facts(seen),
    )  # fmt: skip
    answer = client.complete_json(system, user, SelectionAnswer, LLM_SELECT_TEMPERATURE)
    drafted = build_selection(answer, candidates, facts, transcript, drafts, extended).clips
    selection = SelectionFile(
        clips=finish(drafted, count, facts, transcript, seen, client, reviewer)
    )
    write_json_atomic(video_dir / SELECTION_FILE_NAME, selection)
    return selection


def select_more(
    facts: VideoFacts,
    candidates: Sequence[Candidate],
    transcript: TranscriptFile | None,
    taken: Sequence[Window],
    *,
    count: int,
    extended: bool,
    client: LlmClient,
    evidence: Evidence | None = None,
    reviewer: Reviewer | None = None,
) -> list[SelectionClip]:
    """More clips for a video that has some (D-034): the same selection as `run_select`, but
    the prompt lists the clips already made, no new clip may overlap or come within the gap
    of one, and nothing is cached (each round is a new choice).

    Raises:
        AppError: LLM_AUTH_FAILED, LLM_RATE_LIMITED, or LLM_BAD_RESPONSE from the call.
    """
    if not candidates:
        return []
    candidates = rank_by_speech(candidates, all_words(transcript))
    seen = evidence or Evidence()
    drafts = draft_count(count, reviewer)
    system = select_system(drafts, extended)
    user = select_user(
        facts.title, facts.channel, facts.duration_s, drafts,
        prompt_candidates(candidates, transcript, seen), taken=taken,
        video_facts=video_facts(seen),
    )  # fmt: skip
    answer = client.complete_json(system, user, SelectionAnswer, LLM_SELECT_TEMPERATURE)
    drafted = build_selection(answer, candidates, facts, transcript, drafts, extended, taken).clips
    return finish(drafted, count, facts, transcript, seen, client, reviewer, taken)


# ---------- round 2: the review (D-046) ----------

# (draft id, start, end) -> the close analysis of that draft.
Reviewer = Callable[[int, float, float], ClipAnalysis]


def draft_count(count: int, reviewer: Reviewer | None) -> int:
    """Round 1 drafts REVIEW_DRAFT_FACTOR x the clips wanted when a review follows."""
    if reviewer is None:
        return count
    return max(count, min(REVIEW_DRAFT_MAX, count * REVIEW_DRAFT_FACTOR))


def finish(
    drafts: Sequence[SelectionClip],
    count: int,
    facts: VideoFacts,
    transcript: TranscriptFile | None,
    evidence: Evidence,
    client: LlmClient,
    reviewer: Reviewer | None,
    taken: Sequence[Window] = (),
) -> list[SelectionClip]:
    """The final clips: reviewed when there is a reviewer, then the title step for any clip
    still without LLM text."""
    if reviewer is None or not drafts:
        chosen = list(drafts[:count])
    else:
        chosen = review_clips(drafts, count, facts, transcript, client, reviewer, taken)
    return write_titles(chosen, facts, transcript, evidence, client)


def review_clips(
    drafts: Sequence[SelectionClip],
    count: int,
    facts: VideoFacts,
    transcript: TranscriptFile | None,
    client: LlmClient,
    reviewer: Reviewer,
    taken: Sequence[Window] = (),
) -> list[SelectionClip]:
    """Round 2 (D-046): analyse every draft closely, send the timelines (and frames, when the
    model takes images) to the LLM, and apply its verdicts: kept clips with fixed times and
    text, best first; dropped ones are gone. Drafts it did not mention fill up to `count`.
    If the call fails, the first `count` drafts are used as they are (logged)."""
    # Drafts are analysed REVIEW_WORKERS at a time: most of the time is ffmpeg seeks and OCR.
    with ThreadPoolExecutor(max_workers=REVIEW_WORKERS, thread_name_prefix="review") as pool:
        analyses = list(
            pool.map(lambda pair: reviewer(pair[0], pair[1].start, pair[1].end), enumerate(drafts))
        )
    timelines = [(describe_analysis(a), [label for label, _ in a.frames]) for a in analyses]
    images = [image for a in analyses for _, image in a.frames]
    system = review_system(count, REVIEW_MIN_S)
    user = review_user(facts.title, facts.channel, count, timelines)
    answer: ReviewAnswer | None = None
    for attached in [images, []] if images else [[]]:
        try:
            answer = client.complete_json(
                system, user if attached else _without_images(user), ReviewAnswer,
                LLM_SELECT_TEMPERATURE, images=attached,
            )  # fmt: skip
            break
        except AppError as exc:
            logger.warning("review call failed (images=%s): %s", len(attached), exc.code)
            # A model without image input answers badly; try once more with text only.
            if exc.code != ErrorCode.LLM_BAD_RESPONSE or not attached:
                break
    if answer is None:
        return [
            auto_fixed(draft, analysis) for draft, analysis in zip(drafts, analyses, strict=True)
        ][:count]
    words = all_words(transcript)
    kept: list[SelectionClip] = []
    handled: set[int] = set()
    ordered = sorted(enumerate(answer.clips), key=lambda p: (not p[1].keep, -p[1].score, p[0]))
    for _, verdict in ordered:
        if not 0 <= verdict.id < len(drafts) or verdict.id in handled:
            continue
        handled.add(verdict.id)
        if not verdict.keep:
            logger.info("review dropped draft %s: %s", verdict.id, verdict.problem[:120])
            continue
        if len(kept) >= count:
            continue
        draft, analysis = drafts[verdict.id], analyses[verdict.id]
        clip = apply_verdict(verdict, draft, analysis, facts, words)
        logger.info(
            "review kept draft %s: draft %.1f-%.1f zone %.1f-%.1f answer %s-%s -> %s",
            verdict.id, draft.start, draft.end, analysis.zone_start, analysis.zone_end,
            verdict.start, verdict.end,
            "unusable" if clip is None else f"{clip.start:.1f}-{clip.end:.1f}",
        )  # fmt: skip
        if clip is None:
            # The answer's times cannot be placed: keep the reviewer's text on the draft,
            # with the automatic fixes instead of its times.
            clip = apply_verdict(
                verdict.model_copy(update={"start": None, "end": None}),
                auto_fixed(draft, analysis), analysis, facts, words,
            )  # fmt: skip
        if clip is None:
            continue
        # The code owns the edges (a real review kept the draft times +-1 s on all 5 clips):
        # the rule fixes run on every kept clip.
        clip = auto_fixed(clip, analysis)
        if conflicts(clip.start, clip.end, kept, taken):
            continue
        kept.append(clip)
    rejected = {verdict.id for verdict in answer.clips if not verdict.keep}
    # Drafts the reviewer did not mention fill, auto-fixed; if it kept nothing usable, the
    # drafts it did not drop come next, then (never zero clips) the others.
    for indexes in (
        [i for i in range(len(drafts)) if i not in handled],
        [i for i in range(len(drafts)) if i in handled and i not in rejected],
        [i for i in range(len(drafts)) if i in rejected] if not kept else [],
    ):
        for index in indexes:
            if len(kept) >= count:
                break
            fixed = auto_fixed(drafts[index], analyses[index])
            if any(c.start == fixed.start for c in kept):
                continue
            if not conflicts(fixed.start, fixed.end, kept, taken):
                kept.append(fixed)
    logger.info("review drafts=%s kept=%s images=%s", len(drafts), len(kept), len(images))
    return kept


def _without_images(user: str) -> str:
    return "\n".join(line for line in user.splitlines() if not line.startswith("Images: "))


def auto_fixed(draft: SelectionClip, analysis: ClipAnalysis) -> SelectionClip:
    """The draft fixed by rules (D-046); the code owns the clip edges. In order: skip a
    scoreboard/menu at the start; start AUTO_LEAD_S before late action, but never shorter
    than AUTO_KEEP_S (talk over a held angle keeps its setup); run through action that
    continues just past the end; never across a scene end. When the full fix is too short,
    the scene cut alone is used; the draft only when even that is too short."""
    opening_start = draft.start
    for row in (r for r in analysis.rows if draft.start - 1e-6 <= r.t < draft.end):
        if row.dull < REVIEW_DULL_MIN:
            break
        opening_start = row.t + REVIEW_STEP_S
    start, end = opening_start, draft.end
    inside = [t for t in analysis.actions if start <= t < end]
    if inside and inside[0] - start > AUTO_LATE_S:
        start = max(start, min(inside[0] - AUTO_LEAD_S, end - AUTO_KEEP_S))
    after = [t for t in analysis.actions if end <= t < analysis.zone_end]
    if after and after[0] - end <= AUTO_REACH_S:
        last = after[0]
        for t in after[1:]:
            if t - last > AUTO_GAP_S:
                break
            last = t
        end = min(analysis.zone_end, last + AUTO_TAIL_S, start + CLIP_STANDARD_MAX_S)
    for low, high in ((start, end), (opening_start, draft.end), (draft.start, draft.end)):
        cut_start, cut_end = cut_at_scene_end(low, high, analysis)
        if cut_end - cut_start >= REVIEW_MIN_S:
            start, end = cut_start, cut_end
            break
    else:
        return draft
    if (start, end) == (draft.start, draft.end):
        return draft
    return draft.model_copy(
        update={"start": round(start, TIME_DECIMALS), "end": round(end, TIME_DECIMALS)}
    )


def cut_at_scene_end(start: float, end: float, analysis: ClipAnalysis) -> tuple[float, float]:
    """A clip never runs across a round or scene end (D-046): at an end inside it, keep the
    side with more action (the earlier side on a tie)."""
    for t, _ in analysis.scene_ends:
        if not start + 3 < t < end - 3:
            continue
        before = sum(1 for a in analysis.actions if start <= a < t)
        after = sum(1 for a in analysis.actions if t <= a < end)
        if after > before:
            start = t
        else:
            end = t
    return start, end


def apply_verdict(
    verdict: ReviewClip,
    draft: SelectionClip,
    analysis: ClipAnalysis,
    facts: VideoFacts,
    words: Sequence[Word],
) -> SelectionClip | None:
    """The draft with the reviewer's times (kept inside the analysed zone, whole sentences,
    never across a scene end) and text. None when too short to keep."""
    start = draft.start if verdict.start is None else verdict.start
    end = draft.end if verdict.end is None else verdict.end
    if not (analysis.zone_start - 1 <= start < end <= analysis.zone_end + 1):
        # Times outside the analysed range (another unit, or clip-relative): not usable.
        return None
    start, end = max(analysis.zone_start, start), min(analysis.zone_end, end)
    if end - start < REVIEW_MIN_S:
        return None
    start, end = cut_at_scene_end(start, end, analysis)
    max_len = CLIP_EXTENDED_MAX_S if end - start > CLIP_STANDARD_MAX_S else CLIP_STANDARD_MAX_S
    window = fit_window(start, end, facts.duration_s, max_len, words)
    if window is not None:
        # Sentence snapping may move an edge a few seconds: back inside the scene.
        start, end = cut_at_scene_end(window[0], window[1], analysis)
    if end - start < REVIEW_MIN_S:
        return None
    title = clean_title(verdict.title)
    category = strip_emoji(verdict.category).strip().strip(".:").capitalize()[:20]
    reason = verdict.reason.strip()
    reason = f"{category}: {reason}" if category and reason else reason or draft.reason
    return SelectionClip(
        start=round(start, TIME_DECIMALS),
        end=round(end, TIME_DECIMALS),
        title=title or draft.title,
        youtube_title=truncate_words(
            verdict.youtube_title.strip() or title or draft.youtube_title, YOUTUBE_TITLE_MAX_CHARS
        ),
        youtube_description=truncate_words(
            verdict.youtube_description.strip() or draft.youtube_description,
            DESCRIPTION_MAX_CHARS,
        ),
        hashtags=clean_hashtags(verdict.hashtags) if verdict.hashtags else draft.hashtags,
        linkedin_post=truncate_words(
            verdict.linkedin_post.strip() or draft.linkedin_post, LINKEDIN_MAX_CHARS
        ),
        reason=truncate_words(reason, REASON_MAX_CHARS),
        score=min(1.0, max(0.0, verdict.score)),
        needs_extended=end - start > CLIP_STANDARD_MAX_S,
        from_llm=bool(title) or draft.from_llm,
    )
