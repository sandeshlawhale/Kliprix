"""Stage 10, face crop: `work/<clip>/crop.mp4`, a 1080x1350 face-following crop (docs/05).

Pass 1 samples every 3rd frame at <= 480 px wide and runs the MediaPipe face detector
(Tasks API, short-range model). Pass 2 decodes the cut at full size, crops each frame
around the smoothed face path, and streams it to ffmpeg. Frames are never held in a list.
"""

from __future__ import annotations

import logging
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
import numpy.typing as npt

from app.config import RENDER_TIMEOUT_BASE_S, RENDER_TIMEOUT_PER_CLIP_S
from app.core.errors import AppError, ErrorCode
from app.core.hardware import MemoryInfo, ffmpeg_thread_args, require_ffmpeg
from app.core.proc import CommandFailedError, StreamingCommand
from app.pipeline.face_track import Face, Sample, center_at, face_ratio, track
from app.render import layout_constants as lc
from app.render.layout import crop_left, even, face_crop_box

logger = logging.getLogger(__name__)

FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite"
)
FACE_MODEL_DIR = "mediapipe"
FACE_MODEL_FILE = "blaze_face_short_range.tflite"
CROP_FILE_NAME = "crop.mp4"
MODEL_DOWNLOAD_TIMEOUT_S = 60.0

# "facecam" and "center" are the gaming layouts (D-052).
CropChoice = Literal["auto", "face", "full", "facecam", "center"]


@dataclass(frozen=True)
class CropDecision:
    mode: Literal["face", "full"]
    face_ratio: float
    # The user forced Face but no face was found: a center crop is used (docs/05).
    warning: str | None
    path: list[float]


def ensure_face_model(models_dir: Path) -> Path:
    """The face detector model file, downloaded once into `models/mediapipe/`.

    Raises:
        AppError RENDER_FAILED: it cannot be downloaded.
    """
    target = models_dir / FACE_MODEL_DIR / FACE_MODEL_FILE
    if target.is_file() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".part")
    try:
        with urllib.request.urlopen(
            FACE_MODEL_URL, timeout=MODEL_DOWNLOAD_TIMEOUT_S
        ) as response:  # fixed https URL
            temp.write_bytes(response.read())
        temp.replace(target)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        logger.error("face model download failed: %s", exc)
        raise AppError(
            ErrorCode.RENDER_FAILED,
            "The face detection model could not be downloaded. Check the internet connection.",
        ) from exc
    return target


class FaceDetector:
    """MediaPipe Face Detector in IMAGE mode (one call per sampled frame)."""

    def __init__(self, model_path: Path) -> None:
        from mediapipe.tasks.python import BaseOptions, vision  # heavy: imported on demand

        options = vision.FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=str(model_path)),
            min_detection_confidence=lc.FACE_MIN_CONFIDENCE,
        )
        self._detector: Any = vision.FaceDetector.create_from_options(options)

    def detect(self, rgb: npt.NDArray[np.uint8]) -> list[Face]:
        import mediapipe as mp  # already loaded by __init__

        height, width = rgb.shape[:2]
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        result = self._detector.detect(image)
        faces = []
        for detection in result.detections:
            box = detection.bounding_box
            cx = (box.origin_x + box.width / 2) / width
            faces.append(
                Face(cx=min(1.0, max(0.0, cx)), area=box.width * box.height / (width * height))
            )
        return faces

    def close(self) -> None:
        self._detector.close()


def _timeout(duration_s: float) -> float:
    return RENDER_TIMEOUT_BASE_S + duration_s * RENDER_TIMEOUT_PER_CLIP_S


def analyze_faces(
    cut: Path,
    src_w: int,
    src_h: int,
    duration_s: float,
    detector: FaceDetector,
    *,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    cancel: threading.Event | None = None,
) -> list[Sample]:
    """Faces and scene cuts on every 3rd frame, decoded small."""
    ffmpeg = require_ffmpeg(ffmpeg_path)
    width = min(lc.FACE_ANALYSIS_MAX_W, src_w - src_w % 2)
    height = even(width * src_h / src_w)
    size = width * height * 3
    every = lc.FACE_SAMPLE_EVERY
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", *ffmpeg_thread_args(memory),
        "-i", str(cut), "-an",
        "-vf", f"select=not(mod(n\\,{every})),scale={width}:{height}",
        "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]  # fmt: skip
    samples: list[Sample] = []
    previous: npt.NDArray[np.float32] | None = None
    thumb_h = even(lc.SCENE_CUT_THUMB_W * height / width)
    with StreamingCommand(args, _timeout(duration_s), cancel) as command:
        while True:
            raw = command.read_exact(size)
            if raw is None or len(raw) < size:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
            gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
            small = cv2.resize(gray, (lc.SCENE_CUT_THUMB_W, thumb_h)).astype(np.float32)
            cut_here = previous is not None and float(np.mean(np.abs(small - previous))) > (
                lc.SCENE_CUT_DIFF
            )
            previous = small
            samples.append(Sample(faces=detector.detect(frame), scene_cut=cut_here))
    return samples


def decide(samples: list[Sample], choice: CropChoice, fps: float) -> CropDecision:
    """Crop mode and face path (docs/05 "Auto crop-mode rule")."""
    ratio = face_ratio(samples)
    sample_dt = lc.FACE_SAMPLE_EVERY / fps
    if choice == "full" or (choice == "auto" and ratio < lc.FACE_PRESENT_RATIO):
        return CropDecision("full", ratio, None, [])
    if ratio == 0.0:
        return CropDecision(
            "face", ratio, "No face was found, so the crop is centered.", [0.5] * len(samples)
        )
    return CropDecision("face", ratio, None, track(samples, sample_dt))


def render_crop(
    cut: Path,
    out: Path,
    src_w: int,
    src_h: int,
    duration_s: float,
    path: list[float],
    *,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    cancel: threading.Event | None = None,
) -> Path:
    """Crop every frame of `cut` around `path` and encode `crop.mp4` (video only, 1080x1350).

    Raises:
        AppError RENDER_FAILED: ffmpeg failed.
        CommandCancelledError / CommandTimeoutError.
    """
    ffmpeg = require_ffmpeg(ffmpeg_path)
    box = face_crop_box(src_w, src_h)
    top = (src_h - box.h) // 2
    frame_size = src_w * src_h * 3
    decode = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", *ffmpeg_thread_args(memory),
        "-i", str(cut), "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]  # fmt: skip
    temp = out.with_name(out.stem + ".part.mp4")
    encode = [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
        *ffmpeg_thread_args(memory),
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{lc.FACE_FG_W}x{lc.FACE_FG_H}", "-r", str(lc.FPS), "-i", "pipe:0",
        "-c:v", "libx264", "-preset", lc.INTERMEDIATE_PRESET, "-crf", str(lc.INTERMEDIATE_CRF),
        "-pix_fmt", "yuv420p", str(temp),
    ]  # fmt: skip
    interpolation = cv2.INTER_AREA if box.h >= lc.FACE_FG_H else cv2.INTER_LINEAR
    timeout = _timeout(duration_s)
    frame_index = 0
    try:
        with (
            StreamingCommand(decode, timeout, cancel) as source,
            StreamingCommand(encode, timeout, cancel, stdin_pipe=True, stdout_pipe=False) as sink,
        ):
            while True:
                raw = source.read_exact(frame_size)
                if raw is None or len(raw) < frame_size:
                    break
                frame = np.frombuffer(raw, dtype=np.uint8).reshape(src_h, src_w, 3)
                center = center_at(path, frame_index, lc.FACE_SAMPLE_EVERY)
                left = crop_left(center, src_w, box)
                window = frame[top : top + box.h, left : left + box.w]
                resized = cv2.resize(
                    window, (lc.FACE_FG_W, lc.FACE_FG_H), interpolation=interpolation
                )
                sink.write(resized.tobytes())
                frame_index += 1
            sink.close_stdin()
    except CommandFailedError as exc:
        temp.unlink(missing_ok=True)
        logger.error("face crop ffmpeg failed: %s", exc.stderr_tail)
        raise AppError(ErrorCode.RENDER_FAILED, "Rendering the face crop failed.") from exc
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    temp.replace(out)
    logger.info("crop rendered frames=%s", frame_index)
    return out
