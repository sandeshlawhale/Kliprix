"""Screen type (D-040): what kind of screen each part of the video shows, so menus, lobbies,
character select, loading and black screens are not clipped as highlights.

One frame every 5 s (10 s for videos over 30 min; keyframes only on long videos, like motion)
is scaled to 224x224 and embedded by OpenAI CLIP ViT-B/32 (int8 ONNX, MIT). Each frame is
compared with fixed text labels (`screen_labels.json`, made by `app.cli.screen_labels`) and
the label probabilities are summed per group: gameplay, menu, waiting, talking, other. The
result is `signals/screen.json`. Zero-shot, so it needs no training per game.

The image model (89 MB) is downloaded at first use into `data/models/screen`. onnxruntime is
imported only when the stage runs.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import (
    SCREEN_BATCH,
    SCREEN_DULL_GROUPS,
    SCREEN_DULL_HIGH,
    SCREEN_DULL_LOW,
    SCREEN_FLOOR,
    SCREEN_IMAGE_SIZE,
    SCREEN_LOGIT_SCALE,
    SCREEN_LONG_MIN_S,
    SCREEN_MAX_THREADS,
    SCREEN_MODEL_DISK_GB,
    SCREEN_MODEL_FILE,
    SCREEN_MODEL_REVISION,
    SCREEN_MODEL_URL,
    SCREEN_SMOOTH_S,
    SCREEN_STEP_LONG_S,
    SCREEN_STEP_S,
    SIGNAL_TIMEOUT_BASE_S,
    SIGNAL_TIMEOUT_PER_VIDEO_S,
)
from app.core.files import write_json_atomic
from app.core.hardware import MemoryInfo, ffmpeg_thread_args, require_ffmpeg
from app.core.model_download import Opener, ensure_file
from app.core.proc import CommandCancelledError, StreamingCommand
from app.pipeline.motion import keyframe_mode_gap
from app.pipeline.scoring import FloatArray, moving_average
from app.pipeline.signals import SIGNALS_DIR_NAME
from app.sources.base import SOURCE_FILE_NAME

logger = logging.getLogger(__name__)

LABELS_PATH = Path(__file__).with_name("screen_labels.json")
SCREEN_FILE_NAME = "screen.json"
SCREEN_MODELS_DIR = "screen"
MODEL_LABEL = "screen type model"
SHARE_DECIMALS = 3
# CLIP's image normalization (preprocessor_config.json of the model).
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
_FRAME_BYTES = SCREEN_IMAGE_SIZE * SCREEN_IMAGE_SIZE * 3
# Shown to the LLM (D-043) for each group.
GROUP_NAMES = {
    "gameplay": "gameplay",
    "menu": "menu/lobby/loading",
    "waiting": "black screen",
    "talking": "people talking",
    "other": "other",
}

Progress = Callable[[float], None]


class ScreenFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    revision: str
    step_s: float = Field(gt=0)
    groups: list[str]
    # One row per sampled frame (frame i shows second i * step_s): each group's probability.
    shares: list[list[float]]


@dataclass(frozen=True)
class Labels:
    groups: list[str]
    # Index into `groups` for each label.
    label_group: npt.NDArray[np.int_]
    # Unit-length text embeddings, one row per label.
    embeddings: npt.NDArray[np.float32]


@cache
def load_labels() -> Labels:
    data = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    groups: list[str] = []
    for item in data["labels"]:
        if item["group"] not in groups:
            groups.append(item["group"])
    return Labels(
        groups=groups,
        label_group=np.array([groups.index(item["group"]) for item in data["labels"]]),
        embeddings=np.array([item["embedding"] for item in data["labels"]], dtype=np.float32),
    )


def group_shares(image_embeddings: npt.NDArray[np.float32], labels: Labels) -> FloatArray:
    """Per image, the probability of each group: softmax over all labels (CLIP's zero-shot
    rule), summed by group."""
    norms = np.linalg.norm(image_embeddings, axis=1, keepdims=True)
    unit = image_embeddings / np.maximum(norms, 1e-12)
    logits = SCREEN_LOGIT_SCALE * unit @ labels.embeddings.T
    probs = np.exp(logits - logits.max(axis=1, keepdims=True))
    probs /= probs.sum(axis=1, keepdims=True)
    result = np.zeros((len(unit), len(labels.groups)))
    for index in range(len(labels.groups)):
        result[:, index] = probs[:, labels.label_group == index].sum(axis=1)
    return result


def step_for(duration_s: float) -> int:
    return SCREEN_STEP_LONG_S if duration_s > SCREEN_LONG_MIN_S else SCREEN_STEP_S


def preprocess(raw: bytes) -> npt.NDArray[np.float32]:
    """One RGB24 224x224 frame as CLIP's normalized CHW input."""
    image = np.frombuffer(raw, dtype=np.uint8).reshape(SCREEN_IMAGE_SIZE, SCREEN_IMAGE_SIZE, 3)
    scaled = image.astype(np.float32) / 255.0
    return np.ascontiguousarray(((scaled - _MEAN) / _STD).transpose(2, 0, 1))


# ----- files -----


def screen_path(video_dir: Path) -> Path:
    return video_dir / SIGNALS_DIR_NAME / SCREEN_FILE_NAME


def load_screen(video_dir: Path, duration_s: float) -> ScreenFile | None:
    """The cached screen file when it parses, matches the model revision and covers the video;
    else None."""
    path = screen_path(video_dir)
    if not path.is_file():
        return None
    try:
        screen = ScreenFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None
    expected = int(duration_s // screen.step_s)
    if screen.revision != SCREEN_MODEL_REVISION or abs(len(screen.shares) - expected) > 2:
        return None
    return screen


def model_path(models_dir: Path) -> Path:
    return models_dir / SCREEN_MODELS_DIR / SCREEN_MODEL_REVISION[:12] / SCREEN_MODEL_FILE


def ensure_model(
    models_dir: Path, cancel: threading.Event | None = None, opener: Opener | None = None
) -> Path:
    """The image model file, downloaded at first use.

    Raises:
        AppError: MODEL_DOWNLOAD_FAILED or DISK_FULL.
        CommandCancelledError: `cancel` was set.
    """
    return ensure_file(
        SCREEN_MODEL_URL, model_path(models_dir), label=MODEL_LABEL,
        disk_needed_gb=SCREEN_MODEL_DISK_GB, cancel=cancel, opener=opener,
    )  # fmt: skip


# ----- the stage -----


def _session(model: Path) -> Any:
    import onnxruntime as ort  # heavy: only when the stage runs

    options = ort.SessionOptions()
    options.intra_op_num_threads = SCREEN_MAX_THREADS
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(model), options, providers=["CPUExecutionProvider"])


def screen_series(
    video_dir: Path,
    duration_s: float,
    model: Path,
    *,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    cancel: threading.Event | None = None,
    on_progress: Progress | None = None,
) -> ScreenFile:
    """Classify one frame every `step_for(duration_s)` seconds; a batch of frames is held in
    memory at a time.

    Raises:
        AppError: FFMPEG_MISSING.
        CommandError: ffmpeg failed, timed out, or was cancelled.
    """
    ffmpeg = require_ffmpeg(ffmpeg_path)
    labels = load_labels()
    step = step_for(duration_s)
    gap = keyframe_mode_gap(video_dir / SOURCE_FILE_NAME, duration_s, ffmpeg_path)
    skip = ["-skip_frame", "nokey"] if gap is not None else []
    size = SCREEN_IMAGE_SIZE
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
        *ffmpeg_thread_args(memory), *skip,
        "-i", str(video_dir / SOURCE_FILE_NAME), "-an",
        "-vf", f"fps=1/{step},scale={size}:{size}:flags=bicubic,format=rgb24",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]  # fmt: skip
    timeout = SIGNAL_TIMEOUT_BASE_S + duration_s * SIGNAL_TIMEOUT_PER_VIDEO_S
    session = _session(model)
    input_name = session.get_inputs()[0].name
    expected = max(1, int(duration_s // step))
    rows: list[list[float]] = []
    batch: list[npt.NDArray[np.float32]] = []

    def flush() -> None:
        if not batch:
            return
        embeddings = session.run(None, {input_name: np.stack(batch)})[0]
        shares = group_shares(np.asarray(embeddings, dtype=np.float32), labels)
        rows.extend([[round(float(v), SHARE_DECIMALS) for v in row] for row in shares])
        batch.clear()
        if on_progress is not None:
            on_progress(min(1.0, len(rows) / expected))

    with StreamingCommand(args, timeout, cancel) as command:
        while True:
            if cancel is not None and cancel.is_set():
                raise CommandCancelledError("screen analysis was cancelled")
            raw = command.read_exact(_FRAME_BYTES)
            if raw is None or len(raw) < _FRAME_BYTES:
                break
            batch.append(preprocess(raw))
            if len(batch) >= SCREEN_BATCH:
                flush()
        flush()
    logger.info("screen frames=%s step=%s keyframes=%s", len(rows), step, gap is not None)
    return ScreenFile(
        revision=SCREEN_MODEL_REVISION, step_s=step, groups=labels.groups, shares=rows
    )


def run_screen(
    video_dir: Path,
    duration_s: float,
    models_dir: Path,
    *,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    cancel: threading.Event | None = None,
    on_progress: Progress | None = None,
) -> ScreenFile:
    """Write `signals/screen.json` (skipped when a valid copy exists)."""
    cached = load_screen(video_dir, duration_s)
    if cached is not None:
        return cached
    model = ensure_model(models_dir, cancel)
    screen = screen_series(
        video_dir, duration_s, model, ffmpeg_path=ffmpeg_path, memory=memory,
        cancel=cancel, on_progress=on_progress,
    )  # fmt: skip
    write_json_atomic(screen_path(video_dir), screen)
    return screen


# ----- use in scoring and prompts -----


def dull_series(screen: ScreenFile, length: int) -> FloatArray:
    """Per second, the share of dull screens (menu, waiting), smoothed over SCREEN_SMOOTH_S."""
    dull = [i for i, group in enumerate(screen.groups) if group in SCREEN_DULL_GROUPS]
    if not screen.shares or not dull:
        return np.zeros(length)
    per_sample = np.asarray(screen.shares)[:, dull].sum(axis=1)
    index = np.minimum((np.arange(length) // screen.step_s).astype(int), len(per_sample) - 1)
    return moving_average(per_sample[index], SCREEN_SMOOTH_S)


def screen_factor(dull: FloatArray) -> FloatArray:
    """1 while the dull share is at most SCREEN_DULL_LOW, SCREEN_FLOOR from SCREEN_DULL_HIGH,
    linear in between."""
    position = np.clip((dull - SCREEN_DULL_LOW) / (SCREEN_DULL_HIGH - SCREEN_DULL_LOW), 0, 1)
    return np.asarray(1.0 - (1.0 - SCREEN_FLOOR) * position, dtype=np.float64)


def window_shares(screen: ScreenFile, start: float, end: float) -> dict[str, float]:
    """Mean share of each group over the frames in [start, end)."""
    first = int(start // screen.step_s)
    last = max(first + 1, int(end // screen.step_s))
    rows = screen.shares[first:last]
    if not rows:
        return {}
    means = np.asarray(rows).mean(axis=0)
    return {group: round(float(v), 2) for group, v in zip(screen.groups, means, strict=True)}


def dull_share(shares: dict[str, float]) -> float:
    return sum(shares.get(group, 0.0) for group in SCREEN_DULL_GROUPS)


def describe(shares: dict[str, float], minimum: float = 0.15) -> str:
    """ "gameplay 80%, menu/lobby/loading 20%" for the prompt: groups at or above `minimum`."""
    parts = [
        f"{GROUP_NAMES.get(group, group)} {round(share * 100)}%"
        for group, share in sorted(shares.items(), key=lambda pair: -pair[1])
        if share >= minimum
    ]
    return ", ".join(parts)
