"""Which speech-to-text engine transcribes a video (D-028).

Parakeet runs only when the setting asks for it, the caption language is English (Parakeet
v2 only knows English: on real Hindi audio it wrote nonsense, measured and confirmed by the
owner, which ended the D-029/D-030 experiment), and the PC reaches the 8 GB RAM tier
(Parakeet peaks at 1.5 GB). Otherwise Whisper runs, with the reason, so the UI can say why.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.hardware import MemoryInfo, reaches_normal_tier
from app.core.settings_schema import TranscribeEngine
from app.pipeline.transcribe import Engine

HINGLISH_NOTE = "Hinglish uses Whisper (Parakeet only understands English)."
LOW_RAM_NOTE = "Parakeet needs 8 GB of RAM, so Whisper was used on this PC."


@dataclass(frozen=True)
class EngineChoice:
    engine: Engine
    # Why the setting was not followed; None when it was.
    reason: str | None = None


def choose_engine(
    setting: TranscribeEngine, caption_language: str, memory: MemoryInfo
) -> EngineChoice:
    if setting == "whisper":
        return EngineChoice("whisper")
    if caption_language != "en":
        return EngineChoice("whisper", HINGLISH_NOTE)
    if not reaches_normal_tier(memory):
        return EngineChoice("whisper", LOW_RAM_NOTE)
    return EngineChoice("parakeet")
