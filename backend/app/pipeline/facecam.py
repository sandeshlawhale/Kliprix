"""Facecam detection for gaming clips (D-052).

A streamer's facecam is a small face that stays in one place (an edge or corner of the
frame) while the game moves. The detector misses such small faces on a whole frame scaled
down, so FACECAM_SAMPLES full-size frames are read and each is searched in overlapping
tiles. The cam box is guessed from the face and then snapped to the cam's border: the
strongest still edge near each side, found on the gradient averaged over the samples
(the border stays put while the game and the streamer move).

The detection math (`find_facecam`, `guess_cam`, `refine_cam`) is pure; `analyze_facecam`
does the video I/O. Frames are streamed; only one gradient sum is kept.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Protocol

import cv2
import numpy as np
import numpy.typing as npt

from app.config import RENDER_TIMEOUT_BASE_S, RENDER_TIMEOUT_PER_CLIP_S
from app.core.hardware import MemoryInfo, ffmpeg_thread_args, require_ffmpeg
from app.core.proc import StreamingCommand
from app.render import layout_constants as lc
from app.render.layout import Rect

logger = logging.getLogger(__name__)

FloatImage = npt.NDArray[np.float32]


@dataclass(frozen=True)
class FaceBox:
    """A detected face in source pixels."""

    x: float
    y: float
    w: float
    h: float
    score: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass(frozen=True)
class Facecam:
    cam: Rect
    face: FaceBox  # the median face, used to center the cam crop
    present: float  # share of samples with this face


class BoxDetector(Protocol):
    def detect_boxes(self, rgb: npt.NDArray[np.uint8]) -> list[FaceBox]: ...


def tiles(src_w: int, src_h: int) -> list[Rect]:
    """Overlapping tiles (1/FACECAM_TILE_DIV of the frame), half a tile apart."""
    tile_w, tile_h = src_w // lc.FACECAM_TILE_DIV, src_h // lc.FACECAM_TILE_DIV
    step_x, step_y = max(1, tile_w // 2), max(1, tile_h // 2)
    xs = list(range(0, src_w - tile_w + 1, step_x))
    ys = list(range(0, src_h - tile_h + 1, step_y))
    return [Rect(x, y, tile_w, tile_h) for y in ys for x in xs]


def merge_boxes(boxes: Sequence[FaceBox]) -> list[FaceBox]:
    """One box per face: tiles overlap, so the same face can be found twice."""
    kept: list[FaceBox] = []
    for box in sorted(boxes, key=lambda b: -b.score):
        if all(abs(box.cx - k.cx) > k.w / 2 or abs(box.cy - k.cy) > k.h / 2 for k in kept):
            kept.append(box)
    return kept


def detect_tiled(frame: npt.NDArray[np.uint8], detector: BoxDetector) -> list[FaceBox]:
    """Faces in a full-size frame, searched tile by tile, in frame pixels."""
    height, width = frame.shape[:2]
    found: list[FaceBox] = []
    for tile in tiles(width, height):
        crop = frame[tile.y : tile.bottom, tile.x : tile.right]
        for box in detector.detect_boxes(crop):
            found.append(FaceBox(box.x + tile.x, box.y + tile.y, box.w, box.h, box.score))
    return merge_boxes(found)


def _in_center(face: FaceBox, src_w: int, src_h: int) -> bool:
    margin = (1 - lc.FACECAM_CENTER_ZONE) / 2
    inside_x = margin * src_w < face.cx < (1 - margin) * src_w
    inside_y = margin * src_h < face.cy < (1 - margin) * src_h
    return inside_x and inside_y


def find_facecam(
    samples: Sequence[Sequence[FaceBox]], src_w: int, src_h: int
) -> tuple[FaceBox, float] | None:
    """The facecam face (the median box of its matches) and the share of samples showing
    it, or None. A facecam face is small, stays in place in at least FACECAM_MIN_PRESENT of
    the samples, and is not in the middle of the frame."""
    if not samples:
        return None
    small = [
        [
            f
            for f in faces
            if f.w <= lc.FACECAM_MAX_FACE_W * src_w and not _in_center(f, src_w, src_h)
        ]
        for faces in samples
    ]
    best: list[FaceBox] = []
    for faces in small:
        for face in faces:
            reach = face.w * lc.FACECAM_MATCH_FACES
            matches = []
            for other_faces in small:
                near = [
                    o for o in other_faces
                    if abs(o.cx - face.cx) <= reach and abs(o.cy - face.cy) <= reach
                ]  # fmt: skip
                if near:
                    matches.append(max(near, key=lambda o: o.score))
            if len(matches) > len(best):
                best = matches
    present = len(best) / len(samples)
    if present < lc.FACECAM_MIN_PRESENT:
        return None
    face = FaceBox(
        median(b.x for b in best), median(b.y for b in best),
        median(b.w for b in best), median(b.h for b in best), median(b.score for b in best),
    )  # fmt: skip
    return face, present


def guess_cam(face: FaceBox, src_w: int, src_h: int) -> Rect:
    """A 16:9 cam box around the face, FACECAM_W_PER_FACE face widths wide, inside the frame."""
    w = min(src_w, round(face.w * lc.FACECAM_W_PER_FACE))
    h = min(src_h, round(w / lc.FACECAM_GUESS_ASPECT))
    x = max(0, min(src_w - w, round(face.cx - w / 2)))
    y = max(0, min(src_h - h, round(face.cy - h / 2)))
    return Rect(x, y, w, h)


def _strongest(profile: FloatImage, low: int, high: int) -> int | None:
    """Index of the strongest value in profile[low:high] when it stands out enough."""
    low, high = max(0, low), min(len(profile), high)
    if high - low < 3:
        return None
    band = profile[low:high]
    peak = int(np.argmax(band))
    floor = float(np.median(band))
    if float(band[peak]) < lc.FACECAM_EDGE_RATIO * max(floor, 1e-3):
        return None
    return low + peak


def refine_cam(guess: Rect, grad_x: FloatImage, grad_y: FloatImage, face: FaceBox) -> Rect:
    """Move each side of the guessed box to the cam border (the strongest still edge near
    it). `grad_x`/`grad_y` are the mean absolute horizontal/vertical gradients (frame size).
    A side with no clear edge keeps the guess (often the frame edge itself); a result that
    loses the face or has an odd shape falls back to the guess."""
    reach_x = round(guess.w * lc.FACECAM_EDGE_SEARCH)
    reach_y = round(guess.h * lc.FACECAM_EDGE_SEARCH)
    # Profiles over the middle of the box, so edges of neighbouring overlays count less.
    rows = slice(guess.y + guess.h // 4, guess.bottom - guess.h // 4)
    cols = slice(guess.x + guess.w // 4, guess.right - guess.w // 4)
    column_profile = grad_x[rows].mean(axis=0)
    row_profile = grad_y[:, cols].mean(axis=1)
    face_left, face_right = round(face.x), round(face.x + face.w)
    face_top, face_bottom = round(face.y), round(face.y + face.h)
    left = _strongest(column_profile, guess.x - reach_x, min(face_left, guess.x + reach_x))
    right = _strongest(
        column_profile, max(face_right, guess.right - reach_x), guess.right + reach_x
    )
    top = _strongest(row_profile, guess.y - reach_y, min(face_top, guess.y + reach_y))
    bottom = _strongest(
        row_profile, max(face_bottom, guess.bottom - reach_y), guess.bottom + reach_y
    )
    x0 = guess.x if left is None else left
    x1 = guess.right if right is None else right + 1
    y0 = guess.y if top is None else top
    y1 = guess.bottom if bottom is None else bottom + 1
    cam = Rect(x0, y0, x1 - x0, y1 - y0)
    holds_face = cam.x <= face_left and face_right <= cam.right and cam.y <= face_top
    holds_face = holds_face and face_bottom <= cam.bottom
    if cam.w <= 0 or cam.h <= 0 or not holds_face:
        return guess
    aspect = cam.w / cam.h
    if not lc.FACECAM_MIN_ASPECT <= aspect <= lc.FACECAM_MAX_ASPECT:
        return guess
    return cam


class TiledFaceDetector:
    """MediaPipe Face Detector (short range) returning pixel boxes with scores."""

    def __init__(self, model_path: Path) -> None:
        from mediapipe.tasks.python import BaseOptions, vision  # heavy: imported on demand

        options = vision.FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=str(model_path)),
            min_detection_confidence=lc.FACECAM_MIN_CONFIDENCE,
        )
        self._detector = vision.FaceDetector.create_from_options(options)

    def detect_boxes(self, rgb: npt.NDArray[np.uint8]) -> list[FaceBox]:
        import mediapipe as mp  # already loaded by __init__

        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        boxes = []
        for detection in self._detector.detect(image).detections:
            box = detection.bounding_box
            score = detection.categories[0].score if detection.categories else 0.0
            boxes.append(FaceBox(box.origin_x, box.origin_y, box.width, box.height, score))
        return boxes

    def close(self) -> None:
        self._detector.close()


def analyze_facecam(
    cut: Path,
    src_w: int,
    src_h: int,
    duration_s: float,
    detector: BoxDetector,
    *,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    cancel: threading.Event | None = None,
) -> Facecam | None:
    """The facecam in `cut`, or None when there is none."""
    ffmpeg = require_ffmpeg(ffmpeg_path)
    rate = lc.FACECAM_SAMPLES / max(duration_s, 1.0)
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", *ffmpeg_thread_args(memory),
        "-i", str(cut), "-an", "-vf", f"fps={rate:.6f}",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]  # fmt: skip
    size = src_w * src_h * 3
    samples: list[list[FaceBox]] = []
    grad_x = np.zeros((src_h, src_w), dtype=np.float32)
    grad_y = np.zeros((src_h, src_w), dtype=np.float32)
    timeout = RENDER_TIMEOUT_BASE_S + duration_s * RENDER_TIMEOUT_PER_CLIP_S
    with StreamingCommand(args, timeout, cancel) as command:
        while True:
            raw = command.read_exact(size)
            if raw is None or len(raw) < size:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(src_h, src_w, 3)
            samples.append(detect_tiled(frame, detector))
            gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY).astype(np.float32)
            grad_x += np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
            grad_y += np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    found = find_facecam(samples, src_w, src_h)
    if found is None:
        logger.info("facecam none samples=%d", len(samples))
        return None
    face, present = found
    count = max(1, len(samples))
    guess = guess_cam(face, src_w, src_h)
    cam = refine_cam(guess, grad_x / np.float32(count), grad_y / np.float32(count), face)
    logger.info(
        "facecam found present=%.2f face=%.0fx%.0f@%.0f,%.0f guess=%s cam=%s",
        present, face.w, face.h, face.x, face.y, guess, cam,
    )  # fmt: skip
    return Facecam(cam=cam, face=face, present=present)
