"""Hinglish captions (docs/04 "Hinglish handling", D-016, D-023).

Whisper often writes Hindi in Devanagari. For the words of a selected clip that contain
Devanagari, the LLM converts them to Roman Hinglish with a strict 1:1 word array, so every
word keeps its timing. If that fails, the Devanagari words stay and the captions use the
bundled Noto Sans Devanagari font.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.pipeline.transcribe import Word, contains_devanagari

# Takes the clip's words in order, returns one Roman word per input word, or None on failure.
Transliterator = Callable[[list[str]], list[str] | None]


@dataclass(frozen=True)
class HinglishResult:
    words: list[Word]
    # True when Devanagari is left in the words (captions need the Devanagari font).
    devanagari_left: bool


def needs_transliteration(words: Sequence[Word]) -> bool:
    return any(contains_devanagari(word.text) for word in words)


def to_hinglish(words: Sequence[Word], transliterate: Transliterator) -> HinglishResult:
    """Replace word texts with their Roman Hinglish form, keeping count, order and timing.

    The transliterator is only called when Devanagari is present. A result with a different
    length is rejected (the transliterator does its own retry) and the words stay unchanged.
    """
    original = list(words)
    if not needs_transliteration(original):
        return HinglishResult(original, devanagari_left=False)
    converted = transliterate([word.text for word in original])
    if converted is None or len(converted) != len(original):
        return HinglishResult(original, devanagari_left=True)
    result = [
        word.model_copy(update={"text": text.strip() or word.text})
        for word, text in zip(original, converted, strict=True)
    ]
    return HinglishResult(result, devanagari_left=needs_transliteration(result))
