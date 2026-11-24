"""One clip from source to MP4: cut -> face crop -> captions -> render -> thumbnail
(docs/04 stages 9-12). Used by the job runner and the `render_clip` dev CLI.

`work/<clip>/` keeps `cut.mp4` (clip + handles), `crop.mp4` and `crop.json` (the crop
decision for that cut), so edits inside the handles re-render without cutting again.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.errors import AppError, ErrorCode
from app.core.files import write_json_atomic
from app.core.hardware import MemoryInfo
from app.pipeline.cut import (
    CUT_FILE_NAME,
    CutInfo,
    cut_range,
    load_cut,
    montage_cut_parts,
    run_cut,
    run_montage_cut,
)
from app.pipeline.face_crop import (
    CROP_FILE_NAME,
    CropChoice,
    FaceDetector,
    analyze_faces,
    decide,
    ensure_face_model,
    render_crop,
)
from app.pipeline.facecam import TiledFaceDetector, analyze_facecam
from app.pipeline.parts import Part, total
from app.pipeline.transcribe import Word
from app.render import layout_constants as lc
from app.render.ass import CaptionStyle
from app.render.censor import BlurBox
from app.render.compose import Quality, RenderJob, make_thumbnail, render
from app.render.layout import (
    CropMode,
    Panel,
    Rect,
    center_panels,
    compute_layout,
    facecam_panels,
)

logger = logging.getLogger(__name__)

CROP_INFO_FILE_NAME = "crop.json"
# Called with the clip stage name when it starts: cut, face_crop, captions, render.
StageCallback = Callable[[str], None]


class CropInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    choice: CropChoice
    mode: CropMode
    face_ratio: float
    warning: str | None
    source_start: float
    source_end: float
    # The montage cut's parts (D-051); a crop decided for other parts is not reused.
    parts: list[tuple[float, float]] | None = None
    # Gaming layouts (D-052): whether the clip was treated as gaming, and for "facecam" the
    # cam box [x, y, w, h] and the face center [x, y] in source pixels.
    gaming: bool = False
    cam: list[int] | None = None
    face: list[float] | None = None


@dataclass(frozen=True)
class ClipSpec:
    """A clip to render. `start_s`/`end_s` are source-video seconds; `words` are relative to
    `start_s`. A montage (D-051) plays only `parts` (two or more source ranges), and its
    words and blur boxes are in the montage's own time."""

    start_s: float
    end_s: float
    title: str
    show_title: bool
    style: CaptionStyle
    crop: CropChoice
    blur: int
    words: Sequence[Word]
    blur_boxes: Sequence[BlurBox] = ()
    parts: tuple[Part, ...] = ()
    # A gaming video (D-052): Auto picks the facecam split or the center layout.
    gaming: bool = False

    @property
    def length_s(self) -> float:
        return total(self.parts) if self.parts else self.end_s - self.start_s


@dataclass(frozen=True)
class SourceVideo:
    path: Path | None  # None when the source was deleted (retention policy)
    duration_s: float
    width: int
    height: int


@dataclass(frozen=True)
class RenderResult:
    output: Path
    crop: CropInfo


def _load_crop(work_dir: Path, cut: CutInfo, choice: CropChoice, gaming: bool) -> CropInfo | None:
    path = work_dir / CROP_INFO_FILE_NAME
    if not path.is_file():
        return None
    try:
        info = CropInfo.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None
    same_cut = (
        info.source_start == cut.source_start
        and info.source_end == cut.source_end
        and info.parts == cut.parts
    )
    if not same_cut or info.choice != choice or info.gaming != gaming:
        return None
    if info.mode == "face" and not (work_dir / CROP_FILE_NAME).is_file():
        return None
    return info


def ensure_cut(
    source: SourceVideo,
    work_dir: Path,
    spec: ClipSpec,
    *,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    cancel: threading.Event | None,
) -> CutInfo:
    """A cut that contains [start_s, end_s]: the existing one when it does, else a new cut
    from the source (or RENDER_FAILED when the source was deleted). A montage needs a cut of
    exactly its parts."""
    existing = load_cut(work_dir)
    if spec.parts:
        wanted = montage_cut_parts(spec.parts, source.duration_s)
        if existing is not None and existing.parts is not None:
            same = len(existing.parts) == len(wanted) and all(
                abs(a[0] - b[0]) < 0.01 and abs(a[1] - b[1]) < 0.01
                for a, b in zip(existing.parts, wanted, strict=True)
            )
            if same:
                return existing
        if source.path is None or not source.path.is_file():
            raise AppError(
                ErrorCode.RENDER_FAILED,
                "The source video was deleted, so this montage cannot be cut again.",
            )
        return run_montage_cut(
            source.path, work_dir, wanted, source.width, source.height,
            ffmpeg_path=ffmpeg_path, memory=memory, cancel=cancel,
        )  # fmt: skip
    if (
        existing is not None
        and existing.parts is None
        and existing.source_start <= spec.start_s
        and spec.end_s <= existing.source_end
    ):
        return existing
    if source.path is None or not source.path.is_file():
        raise AppError(
            ErrorCode.RENDER_FAILED,
            "The source video was deleted, so this clip can only be trimmed inside its "
            "saved handles.",
        )
    start, end = cut_range(spec.start_s, spec.end_s, source.duration_s)
    return run_cut(
        source.path, work_dir, start, end, source.width, source.height,
        ffmpeg_path=ffmpeg_path, memory=memory, cancel=cancel,
    )  # fmt: skip


def ensure_crop(
    work_dir: Path,
    cut: CutInfo,
    choice: CropChoice,
    *,
    gaming: bool = False,
    models_dir: Path,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    cancel: threading.Event | None,
) -> CropInfo:
    """The crop decision (and `crop.mp4` for face mode) for this cut and choice."""
    cached = _load_crop(work_dir, cut, choice, gaming)
    if cached is not None:
        return cached
    cut_file = work_dir / CUT_FILE_NAME
    length = total(cut.parts) if cut.parts else cut.source_end - cut.source_start
    if choice in ("facecam", "center") or (choice == "auto" and gaming):
        info = _gaming_crop(
            cut_file, cut, choice, gaming, length,
            models_dir=models_dir, ffmpeg_path=ffmpeg_path, memory=memory, cancel=cancel,
        )  # fmt: skip
        write_json_atomic(work_dir / CROP_INFO_FILE_NAME, info)
        logger.info("crop decided mode=%s cam=%s", info.mode, info.cam)
        return info
    if choice == "full":
        decision_mode, ratio, warning, path = "full", 0.0, None, []
    else:
        detector = FaceDetector(ensure_face_model(models_dir))
        try:
            samples = analyze_faces(
                cut_file, cut.width, cut.height, length, detector,
                ffmpeg_path=ffmpeg_path, memory=memory, cancel=cancel,
            )  # fmt: skip
        finally:
            detector.close()
        decision = decide(samples, choice, lc.FPS)
        decision_mode, ratio, warning, path = (
            decision.mode, decision.face_ratio, decision.warning, decision.path,
        )  # fmt: skip
    if decision_mode == "face":
        render_crop(
            cut_file, work_dir / CROP_FILE_NAME, cut.width, cut.height, length, path,
            ffmpeg_path=ffmpeg_path, memory=memory, cancel=cancel,
        )  # fmt: skip
    info = CropInfo(
        choice=choice,
        mode="face" if decision_mode == "face" else "full",
        face_ratio=round(ratio, 3),
        warning=warning,
        source_start=cut.source_start,
        source_end=cut.source_end,
        parts=cut.parts,
        gaming=gaming,
    )
    write_json_atomic(work_dir / CROP_INFO_FILE_NAME, info)
    logger.info("crop decided mode=%s face_ratio=%.2f", info.mode, info.face_ratio)
    return info


NO_FACECAM_WARNING = "No facecam was found, so the game is shown in the center."


def _gaming_crop(
    cut_file: Path,
    cut: CutInfo,
    choice: CropChoice,
    gaming: bool,
    length: float,
    *,
    models_dir: Path,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    cancel: threading.Event | None,
) -> CropInfo:
    """The gaming layout (D-052): the facecam split when the clip shows a facecam, else the
    center 3:4 of the game. Forced "facecam" without a cam falls back to center with a
    warning."""
    found = None
    if choice != "center":
        detector = TiledFaceDetector(ensure_face_model(models_dir))
        try:
            found = analyze_facecam(
                cut_file, cut.width, cut.height, length, detector,
                ffmpeg_path=ffmpeg_path, memory=memory, cancel=cancel,
            )  # fmt: skip
        finally:
            detector.close()
    base = CropInfo(
        choice=choice, mode="center", face_ratio=0.0, warning=None,
        source_start=cut.source_start, source_end=cut.source_end, parts=cut.parts,
        gaming=gaming,
    )  # fmt: skip
    if found is None:
        return base.model_copy(
            update={"warning": NO_FACECAM_WARNING if choice == "facecam" else None}
        )
    cam = found.cam
    return base.model_copy(
        update={
            "mode": "facecam",
            "cam": [cam.x, cam.y, cam.w, cam.h],
            "face": [round(found.face.cx, 1), round(found.face.cy, 1)],
        }
    )


def layout_panels(crop: CropInfo, src_w: int, src_h: int) -> tuple[Panel, ...]:
    """The source rectangles of the gaming layouts; empty for face and full."""
    if crop.mode == "center":
        return center_panels(src_w, src_h)
    if crop.mode == "facecam" and crop.cam is not None and crop.face is not None:
        return facecam_panels(src_w, src_h, Rect(*crop.cam), crop.face[0], crop.face[1])
    return ()


def render_clip(
    source: SourceVideo,
    work_dir: Path,
    spec: ClipSpec,
    out: Path,
    quality: Quality,
    *,
    models_dir: Path,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    cancel: threading.Event | None = None,
    on_stage: StageCallback | None = None,
) -> RenderResult:
    """Render `spec` to `out`. Stages are cached in `work_dir` where possible.

    Raises:
        AppError: FFMPEG_MISSING, DISK_FULL, RENDER_FAILED.
        CommandCancelledError / CommandTimeoutError.
    """

    def stage(name: str) -> None:
        if on_stage is not None:
            on_stage(name)

    stage("cut")
    cut = ensure_cut(source, work_dir, spec, ffmpeg_path=ffmpeg_path, memory=memory, cancel=cancel)
    stage("face_crop")
    crop = ensure_crop(
        work_dir, cut, spec.crop, gaming=spec.gaming,
        models_dir=models_dir, ffmpeg_path=ffmpeg_path, memory=memory, cancel=cancel,
    )  # fmt: skip
    stage("captions")
    layout = compute_layout(crop.mode, cut.width, cut.height)
    job = RenderJob(
        work_dir=work_dir,
        fg_file=CROP_FILE_NAME if crop.mode == "face" else CUT_FILE_NAME,
        # A montage cut starts with the handle before its first part (D-051).
        offset_s=(spec.parts[0][0] if spec.parts else spec.start_s) - cut.source_start,
        duration_s=spec.length_s,
        layout=layout,
        blur=spec.blur,
        style=spec.style,
        words=spec.words,
        title=spec.title if spec.show_title else None,
        blur_boxes=spec.blur_boxes,
        panels=layout_panels(crop, cut.width, cut.height),
    )
    stage("render")
    output = render(job, out, quality, ffmpeg_path=ffmpeg_path, memory=memory, cancel=cancel)
    return RenderResult(output=output, crop=crop)


__all__ = [
    "ClipSpec",
    "CropInfo",
    "RenderResult",
    "SourceVideo",
    "make_thumbnail",
    "render_clip",
]
