"""Which speech-to-text engine transcribes a video (D-028).

Parakeet runs when the setting asks for it and the PC reaches the 8 GB RAM tier (Parakeet
peaks at 1.5 GB). Otherwise Whisper runs, with the reason, so the UI can say why. Hinglish
also uses Parakeet when selected, on the owner's request to experiment (D-029), although
Parakeet v2 only knows English and writes wrong text for Hindi speech (measured).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.hardware import MemoryInfo, reaches_normal_tier
from app.core.settings_schema import TranscribeEngine
from app.pipeline.transcribe import Engine

LOW_RAM_NOTE = "Parakeet needs 8 GB of RAM, so Whisper was used on this PC."


@dataclass(frozen=True)
class EngineChoice:
    engine: Engine
    # Why the setting was not followed; None when it was.
    reason: str | None = None


def choose_engine(
    setting: TranscribeEngine, memory: MemoryInfo, *, parakeet_only: bool = False
) -> EngineChoice:
    """`parakeet_only` (experiment D-030) ignores the setting and the RAM rule."""
    if parakeet_only:
        return EngineChoice("parakeet")
    if setting == "whisper":
        return EngineChoice("whisper")
    if not reaches_normal_tier(memory):
        return EngineChoice("whisper", LOW_RAM_NOTE)
    return EngineChoice("parakeet")
