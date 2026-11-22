"""On-screen text (D-045): text read from a few frames of each candidate (kill feeds, "ACE",
"VICTORY", "BUY PHASE", game subtitles, item names), so the LLM knows what the screen says.

RapidOCR (PaddleOCR PP-OCRv4 models in ONNX, Apache-2.0, models inside the package) reads 3
frames per candidate: 10 s before the peak (or the likely cause, D-042), the peak, and 10 s
after. Text found on most frames of the video is an overlay (channel name, "follow me on ...")
and is dropped. Results are cached in `signals/screen_text.json` by frame time.

Optional: when the package is missing or fails, selection goes on without it (logged).
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, ValidationError

from app.config import (
    OCR_CANDIDATES_MAX,
    OCR_MIN_CONFIDENCE,
    OCR_OFFSETS_S,
    OCR_OVERLAY_SHARE,
    OCR_TEXTS_MAX,
    OCR_WIDTH,
    PROBE_TIMEOUT_S,
)
from app.core.files import write_json_atomic
from app.core.hardware import require_ffmpeg
from app.core.proc import CommandCancelledError
from app.pipeline.candidates import Candidate
from app.pipeline.signals import SIGNALS_DIR_NAME
from app.sources.base import SOURCE_FILE_NAME

logger = logging.getLogger(__name__)

TEXT_FILE_NAME = "screen_text.json"
# Shortest text kept: at least this many characters and one letter.
MIN_TEXT_CHARS = 3
# Overlay matching works on texts of at least this many letters/digits.
OVERLAY_MIN_CHARS = 5
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")

# Image (BGR, HxWx3 uint8) -> [(text, confidence)].
Reader = Callable[[np.ndarray[Any, np.dtype[np.uint8]]], list[tuple[str, float]]]


class ScreenText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    # Frame time (seconds, as a string key) -> texts read there.
    frames: dict[str, list[str]]


def frame_times(candidates: Sequence[Candidate], duration_s: float) -> list[float]:
    """The frames to read: 3 around the moment of each of the first OCR_CANDIDATES_MAX
    candidates, inside the video, rounded to whole seconds."""
    times: set[float] = set()
    for candidate in candidates[:OCR_CANDIDATES_MAX]:
        center = candidate.cause_t if candidate.cause_t is not None else candidate.peak_t
        for offset in OCR_OFFSETS_S:
            t = float(round(center + offset))
            if 0 <= t < duration_s:
                times.add(t)
    return sorted(times)


def keep_text(text: str) -> bool:
    stripped = text.strip()
    return len(stripped) >= MIN_TEXT_CHARS and any(ch.isalpha() for ch in stripped)


def normalized(text: str) -> str:
    """Letters and digits only, lowercase: OCR splits and joins words differently on each
    frame ("FOLLOW GAMERFLEETON KICK." / "FOLLOWGAMERFLEET ON KICK.")."""
    return _NOT_ALNUM.sub("", text.lower())


def overlay_texts(frames: dict[str, list[str]]) -> set[str]:
    """Normalized texts found on at least OCR_OVERLAY_SHARE of the frames. A frame counts
    when the text appears anywhere in the frame's joined text, so differently split
    readings of the same overlay still count."""
    if len(frames) < 4:
        return set()
    blobs = ["".join(normalized(t) for t in texts) for texts in frames.values()]
    candidates = {normalized(t) for texts in frames.values() for t in texts}
    limit = OCR_OVERLAY_SHARE * len(frames)
    return {
        norm
        for norm in candidates
        if len(norm) >= OVERLAY_MIN_CHARS and sum(norm in blob for blob in blobs) >= limit
    }


def is_overlay(text: str, overlay: set[str]) -> bool:
    """True when the text is an overlay or a piece of one ("ONKICK." of "FOLLOW ... ON
    KICK."), or an overlay is inside it."""
    norm = normalized(text)
    if len(norm) < OVERLAY_MIN_CHARS:
        return norm in overlay
    return any(norm in item or item in norm for item in overlay)


def texts_between(screen_text: ScreenText, start: float, end: float) -> list[str]:
    """Distinct texts read in [start, end), overlays dropped, at most OCR_TEXTS_MAX."""
    overlay = overlay_texts(screen_text.frames)
    found: list[str] = []
    seen: set[str] = set()
    for key in sorted(screen_text.frames, key=float):
        if not start <= float(key) < end:
            continue
        for text in screen_text.frames[key]:
            norm = normalized(text)
            if norm in seen or is_overlay(text, overlay):
                continue
            seen.add(norm)
            found.append(text)
    return found[:OCR_TEXTS_MAX]


# ----- reading frames -----


def grab_frame(
    video_dir: Path, t: float, ffmpeg_path: str | None
) -> np.ndarray[Any, np.dtype[np.uint8]] | None:
    """One frame at `t`, OCR_WIDTH wide, as a BGR array; None when ffmpeg returns nothing."""
    import cv2  # from opencv-contrib-python, already used for the face crop

    ffmpeg = require_ffmpeg(ffmpeg_path)
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{t:.3f}",
        "-i", str(video_dir / SOURCE_FILE_NAME), "-frames:v", "1", "-an",
        "-vf", f"scale={OCR_WIDTH}:-2", "-f", "image2pipe", "-vcodec", "png", "pipe:1",
    ]  # fmt: skip
    try:
        result = subprocess.run(  # noqa: S603 - fixed ffmpeg command, no shell
            args, capture_output=True, timeout=PROBE_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("frame grab failed t=%.1f: %s", t, exc)
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    image = cv2.imdecode(np.frombuffer(result.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
    return None if image is None else np.asarray(image, dtype=np.uint8)


def make_reader() -> Reader:
    from rapidocr_onnxruntime import RapidOCR  # optional package, D-045

    engine = RapidOCR()

    def read(image: np.ndarray[Any, np.dtype[np.uint8]]) -> list[tuple[str, float]]:
        result, _ = engine(image)
        return [(str(item[1]), float(item[2])) for item in result or []]

    return read


def text_path(video_dir: Path) -> Path:
    return video_dir / SIGNALS_DIR_NAME / TEXT_FILE_NAME


def load_text(video_dir: Path) -> ScreenText | None:
    path = text_path(video_dir)
    if not path.is_file():
        return None
    try:
        return ScreenText.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def read_frames(
    video_dir: Path,
    times: Iterable[float],
    *,
    ffmpeg_path: str | None,
    reader: Reader | None = None,
    cancel: threading.Event | None = None,
) -> ScreenText:
    """Read the frames at `times`, reusing those already in the cache, and save the cache."""
    cached = load_text(video_dir)
    frames = dict(cached.frames) if cached is not None else {}
    missing = [t for t in times if f"{t:g}" not in frames]
    if missing:
        read = reader or make_reader()
        for t in missing:
            if cancel is not None and cancel.is_set():
                raise CommandCancelledError("on-screen text was cancelled")
            image = grab_frame(video_dir, t, ffmpeg_path)
            texts = [] if image is None else read(image)
            frames[f"{t:g}"] = [
                text.strip() for text, score in texts
                if score >= OCR_MIN_CONFIDENCE and keep_text(text)
            ]  # fmt: skip
        result = ScreenText(frames=frames)
        write_json_atomic(text_path(video_dir), result)
        logger.info("screen text frames=%s new=%s", len(frames), len(missing))
        return result
    return ScreenText(frames=frames)
