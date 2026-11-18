"""Call 2 (docs/10): Devanagari words to Roman Hinglish with a strict 1:1 word array."""

from __future__ import annotations

import logging

from app.config import LLM_TRANSLITERATE_TEMPERATURE
from app.core.errors import AppError
from app.llm.client import LlmClient
from app.llm.prompts import TRANSLITERATE_REMINDER, TRANSLITERATE_SYSTEM_V1, transliterate_user
from app.llm.schemas import TransliterationAnswer

logger = logging.getLogger(__name__)


def transliterate_words(client: LlmClient, words: list[str]) -> list[str] | None:
    """One Roman word per input word, or None when the LLM fails or keeps the wrong length
    after one reminder (the caller then keeps Devanagari and its font)."""
    user = transliterate_user(words)
    try:
        answer = client.complete_json(
            TRANSLITERATE_SYSTEM_V1, user, TransliterationAnswer, LLM_TRANSLITERATE_TEMPERATURE
        )
        if len(answer) == len(words):
            return answer
        reminder = TRANSLITERATE_REMINDER.format(got=len(answer), expected=len(words))
        answer = client.complete_json(
            TRANSLITERATE_SYSTEM_V1,
            f"{user}\n\n{reminder}",
            TransliterationAnswer,
            LLM_TRANSLITERATE_TEMPERATURE,
        )
    except AppError as exc:
        logger.warning("transliteration failed: %s", exc.code.value)
        return None
    if len(answer) != len(words):
        logger.warning("transliteration length mismatch: %s != %s", len(answer), len(words))
        return None
    return answer
