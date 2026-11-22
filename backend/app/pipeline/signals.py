"""Signal files on a 1-second grid: `signals/<name>.json` = `{version, hz, values}` (docs/08).

Also builds the chat signal (messages per second, reactions weighted, shifted earlier by the
chat delay because chat reacts after the event; D-042 measures the delay when it can).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import (
    CHAT_GROUP_PATTERNS,
    CHAT_HYPE_WEIGHT,
    CHAT_LAG_MAX_S,
    CHAT_LAG_MIN_CORR,
    CHAT_LAG_MIN_GAIN,
    CHAT_LAG_MIN_S,
    CHAT_LAG_S,
)
from app.core.files import write_json_atomic
from app.pipeline.scoring import FloatArray, moving_average, robust_z
from app.sources.base import ChatMessage

SIGNALS_DIR_NAME = "signals"
_GROUP_PATTERNS = {group: re.compile(pattern) for group, pattern in CHAT_GROUP_PATTERNS.items()}
LAG_SMOOTH_S = 9
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


def message_groups(text: str) -> list[str]:
    """The reaction groups (laugh, hype, shock, fail; D-042) a chat message belongs to."""
    lowered = text.lower()
    return [group for group, pattern in _GROUP_PATTERNS.items() if pattern.search(lowered)]


def message_weight(text: str) -> float:
    """1 per message, plus `CHAT_HYPE_WEIGHT` when it is a reaction (any group)."""
    return 1.0 + (CHAT_HYPE_WEIGHT if message_groups(text) else 0.0)


def chat_series(
    messages: Sequence[ChatMessage], duration_s: float, lag: int = CHAT_LAG_S
) -> list[float]:
    """Weighted messages per second, shifted `lag` seconds earlier (docs/04 "Signals")."""
    values = [0.0] * grid_length(duration_s)
    for message in messages:
        second = max(0, int(message.t) - lag)
        if second < len(values):
            values[second] += message_weight(message.text)
    return values


def reaction_counts(messages: Sequence[ChatMessage], length: int) -> FloatArray:
    """Reaction messages (any group) per second, not shifted."""
    counts = np.zeros(length)
    for message in messages:
        second = int(message.t)
        if second < length and message_groups(message.text):
            counts[second] += 1
    return counts


def estimate_lag(reactions: FloatArray, events: FloatArray) -> int | None:
    """The stream's chat delay (D-042): the shift in [CHAT_LAG_MIN_S, CHAT_LAG_MAX_S] at which
    chat reactions best follow the sound events (both smoothed and robust-z scored, the first
    and last 10% of the stream left out). None unless the correlation curve has a clear peak:
    on a busy chat that spams all the time the curve is flat and a fixed guess is better."""
    length = min(len(reactions), len(events))
    low, high = int(length * 0.1), int(length * 0.9) - CHAT_LAG_MAX_S
    if high - low < 10 * CHAT_LAG_MAX_S:
        return None
    sound = robust_z(moving_average(events[:length], LAG_SMOOTH_S))[low:high]
    chat = robust_z(moving_average(reactions[:length], LAG_SMOOTH_S))
    if not sound.any() or not chat.any():
        return None
    curve = [
        float(np.corrcoef(sound, chat[low + lag : high + lag])[0, 1])
        for lag in range(CHAT_LAG_MIN_S, CHAT_LAG_MAX_S + 1)
    ]
    if not all(np.isfinite(curve)):
        return None
    best = int(np.argmax(curve))
    gain = curve[best] - float(np.median(curve))
    if curve[best] < CHAT_LAG_MIN_CORR or gain < CHAT_LAG_MIN_GAIN:
        return None
    return CHAT_LAG_MIN_S + best


def run_chat_signal(
    video_dir: Path, messages: Sequence[ChatMessage], duration_s: float, lag: int = CHAT_LAG_S
) -> SignalFile:
    """Write `signals/chat.json` from the chat replay (cached when valid)."""
    cached = load_signal(video_dir, "chat", duration_s)
    if cached is not None:
        return cached
    return save_signal(video_dir, "chat", chat_series(messages, duration_s, lag))
