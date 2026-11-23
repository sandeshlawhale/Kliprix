"""Utterance segmentation (D-050): word timestamps grouped into coherent spoken units.

An utterance ends after a word ending in sentence punctuation, at a speaker change, or
before a pause of at least `UTTERANCE_PAUSE_S`, except when the word before the pause is a
connector ("and", "the", "ko", ...) and the pause is shorter than `UTTERANCE_STRONG_PAUSE_S`
(the speaker is mid-thought; transcripts often lack punctuation). Utterances longer than
`UTTERANCE_MAX_S` are split into pieces at a comma or the longest pause inside them; piece
edges are soft (acceptable) cut points. Words with invalid times are dropped.

Pure functions, no I/O. Ids are stable for one transcript: "U0", "U1", ... in time order.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass

from app.config import (
    UTTERANCE_CONNECTORS,
    UTTERANCE_MAX_S,
    UTTERANCE_PAUSE_S,
    UTTERANCE_STRONG_PAUSE_S,
)
from app.pipeline.sentences import ends_sentence
from app.pipeline.transcribe import Word

_EDGE_CHARS = ".,!?;:\"'()[]…-—"
_COMMA_MARKS = (",", ";", ":", "—", "-")


@dataclass(frozen=True)
class Utterance:
    id: str
    start: float
    end: float
    text: str
    # Index range [first, last] into `UtteranceIndex.words`.
    first: int
    last: int
    speaker: str | None = None
    # True for a piece of a long utterance that continues after this one.
    continues: bool = False
    # True for a piece that continues an earlier piece.
    continued: bool = False

    @property
    def length(self) -> float:
        return self.end - self.start


def _valid(word: Word) -> bool:
    return (
        math.isfinite(word.start)
        and math.isfinite(word.end)
        and word.start >= 0
        and word.end >= word.start
        and bool(word.text.strip())
    )


def _bare(text: str) -> str:
    return text.strip().lower().strip(_EDGE_CHARS)


def _ends_clause(word: Word) -> bool:
    return word.text.rstrip("\"')] ").endswith(_COMMA_MARKS)


def _clean(words: Sequence[Word]) -> list[tuple[Word, int]]:
    """Valid words in time order with their original index; a word starting before the
    previous one ended starts at that end instead (overlapping pieces), a word fully inside
    the previous one is a duplicate and dropped."""
    order = sorted(
        (i for i, w in enumerate(words) if _valid(w)), key=lambda i: (words[i].start, words[i].end)
    )
    result: list[tuple[Word, int]] = []
    for index in order:
        word = words[index]
        if result and word.start < result[-1][0].end:
            if word.end <= result[-1][0].end:
                continue
            word = word.model_copy(update={"start": result[-1][0].end})
        result.append((word, index))
    return result


def clean_words(words: Sequence[Word]) -> list[Word]:
    """Valid words in time order, overlaps removed (see `_clean`)."""
    return [word for word, _ in _clean(words)]


def segment(
    words: Sequence[Word],
    speakers: Sequence[str | None] | None = None,
    pause_s: float = UTTERANCE_PAUSE_S,
    strong_pause_s: float = UTTERANCE_STRONG_PAUSE_S,
    max_s: float = UTTERANCE_MAX_S,
) -> UtteranceIndex:
    """Utterances of `words`. `speakers`, when given, is one label per word (same order as
    `words`, before cleaning); a change of label ends an utterance."""
    if speakers is not None and len(speakers) != len(words):
        raise ValueError("speakers must have one label per word")
    kept = _clean(words)
    cleaned = [word for word, _ in kept]
    labels: list[str | None] = [speakers[i] if speakers is not None else None for _, i in kept]
    groups: list[tuple[int, int]] = []
    begin = 0
    for index, word in enumerate(cleaned):
        following = cleaned[index + 1] if index + 1 < len(cleaned) else None
        if following is None:
            groups.append((begin, index))
            break
        pause = following.start - word.end
        speaker_change = labels[index] != labels[index + 1] and labels[index + 1] is not None
        connector = _bare(word.text) in UTTERANCE_CONNECTORS
        boundary = (
            ends_sentence(word)
            or speaker_change
            or pause >= strong_pause_s
            or (pause >= pause_s and not connector)
        )
        if boundary:
            groups.append((begin, index))
            begin = index + 1
    pieces: list[tuple[int, int, bool, bool]] = []
    for first, last in groups:
        pieces += _split_long(cleaned, first, last, max_s)
    utterances = [
        Utterance(
            id=f"U{n}",
            start=cleaned[first].start,
            end=cleaned[last].end,
            text=" ".join(w.text.strip() for w in cleaned[first : last + 1]),
            first=first,
            last=last,
            speaker=labels[first],
            continues=continues,
            continued=continued,
        )
        for n, (first, last, continues, continued) in enumerate(pieces)
    ]
    return UtteranceIndex(words=cleaned, utterances=utterances)


def _split_long(
    words: Sequence[Word], first: int, last: int, max_s: float
) -> list[tuple[int, int, bool, bool]]:
    """[first, last] as one piece, or split recursively at the best cut inside: after a
    comma-like mark, else at the longest pause (the middle on a tie)."""
    if words[last].end - words[first].start <= max_s or last == first:
        return [(first, last, False, False)]
    middle = (words[first].start + words[last].end) / 2

    def quality(i: int) -> tuple[int, float, float]:
        pause = words[i + 1].start - words[i].end
        return (int(_ends_clause(words[i])), round(pause, 2), -abs(words[i].end - middle))

    cut = max(range(first, last), key=quality)
    left = _split_long(words, first, cut, max_s)
    right = _split_long(words, cut + 1, last, max_s)
    left[-1] = (left[-1][0], left[-1][1], True, left[-1][3])
    right[0] = (right[0][0], right[0][1], right[0][2], True)
    return left + right


@dataclass(frozen=True)
class UtteranceIndex:
    words: list[Word]
    utterances: list[Utterance]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_starts", [u.start for u in self.utterances])
        object.__setattr__(self, "_by_id", {u.id: u for u in self.utterances})

    def get(self, utterance_id: str) -> Utterance | None:
        by_id: dict[str, Utterance] = self._by_id  # type: ignore[attr-defined]
        return by_id.get(utterance_id.strip().upper())

    def between(self, start: float, end: float) -> list[Utterance]:
        """Utterances overlapping [start, end)."""
        return [u for u in self.utterances if u.end > start and u.start < end]

    def at(self, t: float, tolerance: float = 0.0) -> Utterance | None:
        """The utterance that `t` falls strictly inside (more than `tolerance` from both of
        its edges), if any."""
        starts: list[float] = self._starts  # type: ignore[attr-defined]
        index = bisect.bisect_right(starts, t) - 1
        if index < 0:
            return None
        utterance = self.utterances[index]
        if utterance.start + tolerance < t < utterance.end - tolerance:
            return utterance
        return None

    def word_at(self, t: float, tolerance: float = 0.0) -> Word | None:
        """The word that `t` cuts (strictly inside it, beyond `tolerance`)."""
        for word in self.words:
            if word.start > t:
                return None
            if word.start + tolerance < t < word.end - tolerance:
                return word
        return None

    def previous(self, utterance: Utterance) -> Utterance | None:
        index = int(utterance.id[1:])
        return self.utterances[index - 1] if index > 0 else None

    def following(self, utterance: Utterance) -> Utterance | None:
        index = int(utterance.id[1:])
        return self.utterances[index + 1] if index + 1 < len(self.utterances) else None

    def first_word(self, utterance: Utterance) -> str:
        return _bare(self.words[utterance.first].text)

    def last_word(self, utterance: Utterance) -> str:
        return _bare(self.words[utterance.last].text)

    def has_speech(self, start: float, end: float) -> bool:
        return any(w.end > start and w.start < end for w in self.words)

    def speech_edges(self, start: float, end: float) -> tuple[float, float] | None:
        """(first word start, last word end) inside [start, end), or None."""
        inside = [w for w in self.words if w.end > start and w.start < end]
        if not inside:
            return None
        return inside[0].start, inside[-1].end
