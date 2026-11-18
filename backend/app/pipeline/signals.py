"""Signal files on a 1-second grid: `signals/<name>.json` = `{version, hz, values}` (docs/08).

Also builds the chat signal (messages per second, hype tokens weighted, shifted earlier by
`CHAT_LAG_S` because chat reacts after the event).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import CHAT_HYPE_TOKENS, CHAT_HYPE_WEIGHT, CHAT_LAG_S
from app.core.files import write_json_atomic
from app.sources.base import ChatMessage

SIGNALS_DIR_NAME = "signals"
# A cached signal must cover the video length within this many seconds.
LENGTH_TOLERANCE_S = 2


class SignalFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    hz: Literal[1] = 1
    values: list[float] = Field(min_length=1)


def grid_length(duration_s: float) -> int:
    """Number of 1-second bins covering a video of `duration_s` seconds."""
    return max(1, math.ceil(duration_s))


def signal_path(video_dir: Path, name: str) -> Path:
    return video_dir / SIGNALS_DIR_NAME / f"{name}.json"


def load_signal(video_dir: Path, name: str, duration_s: float) -> SignalFile | None:
    """The cached signal if it parses and covers the video length, else None."""
    path = signal_path(video_dir, name)
    if not path.is_file():
        return None
    try:
        signal = SignalFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None
    if abs(len(signal.values) - grid_length(duration_s)) > LENGTH_TOLERANCE_S:
        return None
    if not all(math.isfinite(value) for value in signal.values):
        return None
    return signal


def save_signal(video_dir: Path, name: str, values: list[float]) -> SignalFile:
    signal = SignalFile(values=values)
    write_json_atomic(signal_path(video_dir, name), signal)
    return signal


def message_weight(text: str) -> float:
    """1 per message, plus `CHAT_HYPE_WEIGHT` when it contains a laugh/hype token."""
    lowered = text.lower()
    hype = any(token in lowered for token in CHAT_HYPE_TOKENS)
    return 1.0 + (CHAT_HYPE_WEIGHT if hype else 0.0)


def chat_series(messages: list[ChatMessage], duration_s: float) -> list[float]:
    """Weighted messages per second, shifted `CHAT_LAG_S` earlier (docs/04 "Signals")."""
    values = [0.0] * grid_length(duration_s)
    for message in messages:
        second = int(message.t) - CHAT_LAG_S
        if second < 0:
            second = 0
        if second < len(values):
            values[second] += message_weight(message.text)
    return values


def run_chat_signal(video_dir: Path, messages: list[ChatMessage], duration_s: float) -> SignalFile:
    """Write `signals/chat.json` from the chat replay (cached when valid)."""
    cached = load_signal(video_dir, "chat", duration_s)
    if cached is not None:
        return cached
    return save_signal(video_dir, "chat", chat_series(messages, duration_s))
