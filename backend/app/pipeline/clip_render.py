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
from app.pipeline.cut import CUT_FILE_NAME, CutInfo, cut_range, load_cut, run_cut
from app.pipeline.face_crop import (
    CROP_FILE_NAME,
    CropChoice,
    FaceDetector,
    analyze_faces,
    decide,
    ensure_face_model,
    render_crop,
)
from app.pipeline.transcribe import Word
from app.render import layout_constants as lc
from app.render.ass import CaptionStyle
from app.render.compose import Quality, RenderJob, make_thumbnail, render
from app.render.layout import compute_layout

logger = logging.getLogger(__name__)

CROP_INFO_FILE_NAME = "crop.json"
# Called with the clip stage name when it starts: cut, face_crop, captions, render.
StageCallback = Callable[[str], None]


class CropInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    choice: CropChoice
    mode: Literal["face", "full"]
    face_ratio: float
    warning: str | None
    source_start: float
    source_end: float


@dataclass(frozen=True)
class ClipSpec:
    """A clip to render. `start_s`/`end_s` are source-video seconds; `words` are relative to
    `start_s`."""

    start_s: float
    end_s: float
    title: str
    show_title: bool
    style: CaptionStyle
    crop: CropChoice
    blur: int
    words: Sequence[Word]


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


def _load_crop(work_dir: Path, cut: CutInfo, choice: CropChoice) -> CropInfo | None:
    path = work_dir / CROP_INFO_FILE_NAME
    if not path.is_file():
        return None
    try:
        info = CropInfo.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None
    same_cut = info.source_start == cut.source_start and info.source_end == cut.source_end
    if not same_cut or info.choice != choice:
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
    from the source (or RENDER_FAILED when the source was deleted)."""
    existing = load_cut(work_dir)
    if (
        existing is not None
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
    models_dir: Path,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    cancel: threading.Event | None,
) -> CropInfo:
    """The crop decision (and `crop.mp4` for face mode) for this cut and choice."""
    cached = _load_crop(work_dir, cut, choice)
    if cached is not None:
        return cached
    cut_file = work_dir / CUT_FILE_NAME
    length = cut.source_end - cut.source_start
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
    )
    write_json_atomic(work_dir / CROP_INFO_FILE_NAME, info)
    logger.info("crop decided mode=%s face_ratio=%.2f", info.mode, info.face_ratio)
    return info


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
        work_dir, cut, spec.crop,
        models_dir=models_dir, ffmpeg_path=ffmpeg_path, memory=memory, cancel=cancel,
    )  # fmt: skip
    stage("captions")
    layout = compute_layout(crop.mode, cut.width, cut.height)
    job = RenderJob(
        work_dir=work_dir,
        fg_file=CROP_FILE_NAME if crop.mode == "face" else CUT_FILE_NAME,
        offset_s=spec.start_s - cut.source_start,
        duration_s=spec.end_s - spec.start_s,
        layout=layout,
        blur=spec.blur,
        style=spec.style,
        words=spec.words,
        title=spec.title if spec.show_title else None,
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
