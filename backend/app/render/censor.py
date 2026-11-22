"""Censoring of bad words in rendered clips (D-048).

Caption words and the on-video title get the middle half of a bad word's letters replaced by
`*` ("fuck" -> "f**k"). The stored words are never changed: only the render is censored.
`bleep_spans` gives the times whose audio is muted and covered by a beep: every listed word
and every word the speech-to-text already wrote with `*` ("F***", "Bh**").
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

from app.pipeline.transcribe import Word

# Whole words (letters only, lowercase). Hinglish words are listed in the spellings Whisper
# and the transliteration step produce.
BAD_WORDS: frozenset[str] = frozenset(
    {
        # English
        "fuck", "fucks", "fucked", "fucker", "fuckers", "fucking", "fuckin", "fucky",
        "motherfucker", "motherfuckers", "motherfucking", "fuckboy", "fuckoff",
        "shit", "shits", "shitty", "shitting", "bullshit", "horseshit",
        "bitch", "bitches", "bitching", "bitchy", "sonofabitch",
        "bastard", "bastards", "asshole", "assholes", "dick", "dicks", "dickhead",
        "cunt", "cunts", "pussy", "pussies", "cock", "cocks", "cocksucker",
        "whore", "whores", "slut", "sluts", "nigga", "niggas", "nigger", "niggers",
        "faggot", "faggots", "fag", "wanker", "twat", "porn", "porno",
        # Hinglish (Roman)
        "bc", "mc", "bkl", "bsdk", "mkc", "mkb", "tmkc",
        "behenchod", "behenchodd", "bhenchod", "behnchod", "benchod", "bhanchod",
        "behenchodo", "bhencho", "behencho", "bhenchodd",
        "madarchod", "maderchod", "madarchodd", "madarchodo", "madarjaat",
        "betichod", "chutiya", "chutiye", "chutiyapa", "chutiyo", "chootiya", "chutia",
        "chut", "choot", "bhosdi", "bhosdike", "bhosdika", "bhosdiwale", "bhosda",
        "bhosadi", "bhosadike", "bhosdiwala", "lawda", "lawde", "lavda", "lavde",
        "lauda", "laude", "lode", "lodu", "loda", "lund", "lundo", "randi", "randiya",
        "randwa", "randibaaz", "gaand", "gand", "gandu", "gaandu", "harami", "haramkhor",
        "haramzada", "haramzade", "tatte", "tatto", "jhaant", "jhant", "jhatu", "jhaatu",
        "chodu", "chodna", "chudai", "chudi", "kutiya", "kamine",
    }
)  # fmt: skip
# Devanagari spellings (compared on the word with punctuation stripped).
BAD_DEVANAGARI: frozenset[str] = frozenset(
    {
        "चूतिया", "चुतिया", "भेनचोद", "बहनचोद", "भेंचोद", "मादरचोद", "भोसड़ी",
        "भोसडी", "भोसड़ीके", "लौड़ा", "लौड़े", "लोड़े", "लवड़ा", "लंड", "गांड", "गाँड",
        "गांडू", "रंडी", "हरामी", "हरामज़ादा", "चूत", "चोदू",
    }
)  # fmt: skip
# Not listed on purpose: "chod" (as often "chhod", leave, in Roman Hindi), "saala" (also
# brother-in-law), "kutta" (dog), "damn".
BLEEP_PAD_S = 0.05


def _key(text: str) -> str:
    """Letters and combining marks (Devanagari vowel signs) only, lowercase."""
    return "".join(ch for ch in text if unicodedata.category(ch)[0] in "LM").lower()


def is_bad(text: str) -> bool:
    """True when the word (punctuation and case ignored) is on the list."""
    key = _key(text)
    if not key:
        return False
    if key in BAD_DEVANAGARI:
        return True
    return key in BAD_WORDS


def already_starred(text: str) -> bool:
    """A word the speech-to-text wrote censored ("F***", "Bh**", "l*de")."""
    return "*" in text and any(ch.isalpha() for ch in text)


def mask(text: str) -> str:
    """Replace the middle half of the letters of `text` with `*`, keeping punctuation:
    "fuck!" -> "f**k!", "chutiya" -> "ch****a", "bc" -> "b*"."""
    letters = [i for i, ch in enumerate(text) if ch.isalpha()]
    count = len(letters)
    if count == 0:
        return text
    keep_front = max(1, -(-count // 4))  # ceil(count / 4)
    keep_back = count // 4
    hidden = set(letters[keep_front : count - keep_back]) or set(letters[-1:])
    return "".join("*" if i in hidden else ch for i, ch in enumerate(text))


def censor_text(text: str) -> str:
    """Mask every bad word of a sentence (titles)."""
    return " ".join(mask(word) if is_bad(word) else word for word in text.split(" "))


def censor_words(words: Sequence[Word]) -> list[Word]:
    """Copies of the words with bad ones masked."""
    return [
        word.model_copy(update={"text": mask(word.text)}) if is_bad(word.text) else word
        for word in words
    ]


def bleep_spans(words: Sequence[Word], duration_s: float) -> list[tuple[float, float]]:
    """Merged [start, end] times (clip seconds) to mute and beep."""
    spans: list[tuple[float, float]] = []
    for word in sorted(words, key=lambda w: w.start):
        if not (is_bad(word.text) or already_starred(word.text)):
            continue
        start = round(max(0.0, word.start - BLEEP_PAD_S), 3)
        end = round(min(duration_s, word.end + BLEEP_PAD_S), 3)
        if end <= start:
            continue
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    return spans
