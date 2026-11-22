"""Sound events (D-041): what kind of sound each part of the video has (gunfire, explosions,
shouting, laughter, cheering, crashes, ...), so highlights are found by what is heard rather
than by how loud it is. Works without chat and without speech.

`audio.wav` is cut into 2 s windows; each is tagged by CED-mini (AudioSet's 527 classes,
Apache-2.0) through sherpa-onnx, which the app already uses for Parakeet. The classes are
summed into groups (`SOUND_GROUPS`, the strongest class per group). Two files:
- `signals/sound.json`: the per-second excitement (weighted sum of the action groups), a normal
  signal on the 1-second grid that candidates score like the others;
- `signals/sound_events.json`: each window's group values (>= 0.1), for the LLM's description
  of a moment (D-043) and the chat delay estimate (D-042).

The model (47 MB) is downloaded at first use into `data/models/sound`. sherpa-onnx is imported
only when the stage runs.
"""

from __future__ import annotations

import logging
import threading
import wave
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import (
    AUDIO_SAMPLE_RATE,
    SOUND_EXCITEMENT,
    SOUND_GROUPS,
    SOUND_MAX_THREADS,
    SOUND_MODEL_DISK_GB,
    SOUND_MODEL_NAME,
    SOUND_MODEL_URL,
    SOUND_SAVE_MIN,
    SOUND_WINDOW_S,
)
from app.core.files import write_json_atomic
from app.core.model_download import Opener, ensure_archive
from app.core.proc import CommandCancelledError
from app.pipeline.signals import (
    SIGNALS_DIR_NAME,
    SignalFile,
    grid_length,
    load_signal,
    save_signal,
)

logger = logging.getLogger(__name__)

EVENTS_FILE_NAME = "sound_events.json"
SOUND_MODELS_DIR = "sound"
MODEL_LABEL = "sound event model"
MODEL_FILES = ("model.int8.onnx", "class_labels_indices.csv")
VALUE_DECIMALS = 2
PCM16_SCALE = 32768.0
# All 527 AudioSet classes, so every group's classes are scored.
TOP_K = 527

Progress = Callable[[float], None]
# Class name -> probability for one window.
Tagger = Callable[[np.ndarray[Any, np.dtype[np.float32]]], dict[str, float]]


class SoundEvents(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    window_s: float = Field(gt=0)
    # One entry per window (window i starts at i * window_s): group -> value, only >= 0.1.
    windows: list[dict[str, float]]


def group_values(classes: dict[str, float]) -> dict[str, float]:
    """Each group's value: its strongest class."""
    return {
        group: max((classes.get(name, 0.0) for name in names), default=0.0)
        for group, names in SOUND_GROUPS.items()
    }


def excitement(groups: dict[str, float]) -> float:
    return sum(weight * groups.get(group, 0.0) for group, weight in SOUND_EXCITEMENT.items())


def per_second(windows: Sequence[dict[str, float]], window_s: float, length: int) -> list[float]:
    """The excitement of each window, repeated over its seconds, on a `length`-second grid."""
    values = [0.0] * length
    for index, groups in enumerate(windows):
        first = int(index * window_s)
        value = excitement(groups)
        for second in range(first, min(length, first + int(window_s))):
            values[second] = value
    return values


# ----- model -----


def model_dir(models_dir: Path) -> Path:
    return models_dir / SOUND_MODELS_DIR / SOUND_MODEL_NAME


def ensure_model(
    models_dir: Path, cancel: threading.Event | None = None, opener: Opener | None = None
) -> Path:
    """The model folder, downloaded and unpacked at first use.

    Raises:
        AppError: MODEL_DOWNLOAD_FAILED or DISK_FULL.
        CommandCancelledError: `cancel` was set.
    """
    return ensure_archive(
        SOUND_MODEL_URL, models_dir / SOUND_MODELS_DIR, SOUND_MODEL_NAME, MODEL_FILES,
        label=MODEL_LABEL, disk_needed_gb=SOUND_MODEL_DISK_GB, cancel=cancel, opener=opener,
    )  # fmt: skip


def make_tagger(folder: Path) -> Tagger:
    import sherpa_onnx  # heavy: only when the stage runs

    config = sherpa_onnx.AudioTaggingConfig(
        model=sherpa_onnx.AudioTaggingModelConfig(
            ced=str(folder / MODEL_FILES[0]), num_threads=SOUND_MAX_THREADS
        ),
        labels=str(folder / MODEL_FILES[1]),
        top_k=TOP_K,
    )
    tagger = sherpa_onnx.AudioTagging(config)

    def tag(samples: np.ndarray[Any, np.dtype[np.float32]]) -> dict[str, float]:
        stream = tagger.create_stream()
        stream.accept_waveform(AUDIO_SAMPLE_RATE, samples)
        return {event.name: float(event.prob) for event in tagger.compute(stream)}

    return tag


# ----- the stage -----


def events_path(video_dir: Path) -> Path:
    return video_dir / SIGNALS_DIR_NAME / EVENTS_FILE_NAME


def load_events(video_dir: Path, duration_s: float) -> SoundEvents | None:
    path = events_path(video_dir)
    if not path.is_file():
        return None
    try:
        events = SoundEvents.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None
    if abs(len(events.windows) - duration_s / events.window_s) > 2:
        return None
    return events


def tag_wav(
    wav: Path,
    tag: Tagger,
    cancel: threading.Event | None = None,
    on_progress: Progress | None = None,
) -> SoundEvents:
    """Tag `wav` window by window; one window of samples is in memory at a time."""
    frames_per_window = SOUND_WINDOW_S * AUDIO_SAMPLE_RATE
    windows: list[dict[str, float]] = []
    with wave.open(str(wav), "rb") as reader:
        total = max(1, reader.getnframes())
        while True:
            if cancel is not None and cancel.is_set():
                raise CommandCancelledError("sound analysis was cancelled")
            raw = reader.readframes(frames_per_window)
            if not raw:
                break
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / PCM16_SCALE
            # The model rejects a very short input: the last window is padded with silence.
            if len(samples) < frames_per_window:
                samples = np.pad(samples, (0, frames_per_window - len(samples)))
            groups = group_values(tag(samples))
            windows.append(
                {
                    group: round(value, VALUE_DECIMALS)
                    for group, value in groups.items()
                    if value >= SOUND_SAVE_MIN
                }
            )
            if on_progress is not None:
                on_progress(min(1.0, reader.tell() / total))
    return SoundEvents(window_s=SOUND_WINDOW_S, windows=windows)


def run_sound(
    video_dir: Path,
    wav: Path,
    duration_s: float,
    models_dir: Path,
    *,
    cancel: threading.Event | None = None,
    on_progress: Progress | None = None,
) -> tuple[SignalFile, SoundEvents]:
    """Write `signals/sound_events.json` and `signals/sound.json` (each reused when valid).

    Raises:
        AppError: MODEL_DOWNLOAD_FAILED or DISK_FULL.
        CommandCancelledError: `cancel` was set.
    """
    events = load_events(video_dir, duration_s)
    if events is None:
        folder = ensure_model(models_dir, cancel)
        events = tag_wav(wav, make_tagger(folder), cancel, on_progress)
        write_json_atomic(events_path(video_dir), events)
        logger.info("sound windows=%s", len(events.windows))
    signal = load_signal(video_dir, "sound", duration_s)
    if signal is None:
        values = per_second(events.windows, events.window_s, grid_length(duration_s))
        signal = save_signal(video_dir, "sound", values)
    return signal, events


# ----- use in prompts -----

# Shown to the LLM (D-043): the action groups only; speech and music are background.
EVENT_NAMES = {
    "gunfire": "gunfire",
    "explosion": "explosion",
    "shouting": "shouting",
    "laughter": "laughter",
    "cheering": "cheering/crowd",
    "crash": "crash/glass",
    "vehicle": "skidding/siren",
}


def timeline(
    events: SoundEvents, start: float, end: float, minimum: float = 0.2, limit: int = 12
) -> list[tuple[float, str]]:
    """Moments in [start, end) where an action group is at least `minimum`: (time, "gunfire,
    shouting"). Neighbouring windows with the same groups are merged; at most `limit`."""
    result: list[tuple[float, str]] = []
    previous = ""
    first = max(0, int(start // events.window_s))
    last = min(len(events.windows), int(end // events.window_s) + 1)
    for index in range(first, last):
        groups = events.windows[index]
        names = [
            EVENT_NAMES[group]
            for group, value in sorted(groups.items(), key=lambda pair: -pair[1])
            if group in EVENT_NAMES and value >= minimum
        ]
        text = ", ".join(names)
        if text and text != previous:
            result.append((index * events.window_s, text))
        previous = text
    return result[:limit]


def window_share(events: SoundEvents, start: float, end: float, group: str) -> float:
    """Mean value of one group over the windows in [start, end)."""
    first = max(0, int(start // events.window_s))
    last = min(len(events.windows), max(first + 1, int(end // events.window_s)))
    rows = events.windows[first:last]
    if not rows:
        return 0.0
    return float(np.mean([row.get(group, 0.0) for row in rows]))
