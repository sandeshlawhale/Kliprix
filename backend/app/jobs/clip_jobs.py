"""`clip_render` (full-quality export) and `clip_preview` (540x960) jobs, plus the source
retention policy after a video's clips finish (docs/04, 08)."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from app.core.errors import AppError, ErrorCode
from app.core.metrics import stage_timer
from app.db.models import ClipRow
from app.db.repositories import batches as repo
from app.db.repositories.settings import load_settings
from app.jobs.context import JobContext, slugify
from app.pipeline.audio import AUDIO_FILE_NAME
from app.pipeline.clip_render import ClipSpec, SourceVideo, make_thumbnail, render_clip
from app.pipeline.download import load_cached as load_download
from app.pipeline.transcribe import Word
from app.render.censor import BlurBox
from app.sources.base import SOURCE_FILE_NAME

logger = logging.getLogger(__name__)


def clip_words(clip: ClipRow) -> list[Word]:
    return [Word.model_validate(item) for item in json.loads(clip.words_json or "[]")]


def clip_blur_boxes(clip: ClipRow) -> list[BlurBox]:
    return [BlurBox.model_validate(item) for item in json.loads(clip.blur_boxes_json or "[]")]


def export_path(ctx: JobContext, clip: ClipRow, video_title: str | None) -> Path:
    """exports/<batch>/<video_slug>/clip_NN_<slug>.mp4 (docs/08)."""
    folder = ctx.data_dir / "exports" / clip.batch_id / slugify(video_title or "", "video")
    return folder / f"clip_{clip.idx + 1:02d}_{slugify(clip.title or '')}.mp4"


class ClipJob:
    def __init__(
        self, ctx: JobContext, clip_id: str, preview: bool, cancel: threading.Event
    ) -> None:
        self.ctx = ctx
        self.clip_id = clip_id
        self.preview = preview
        self.cancel = cancel

    def _update(self, **fields: Any) -> None:
        with self.ctx.session_factory() as session:
            clip = repo.get_clip(session, self.clip_id)
            if clip is None:
                raise AppError(ErrorCode.NOT_FOUND, "The clip was deleted.")
            for key, value in fields.items():
                setattr(clip, key, value)
            session.commit()
            self.ctx.publish(
                clip.batch_id, "clip", id=clip.id, status=clip.status, stage=clip.stage,
                progress=clip.progress,
            )  # fmt: skip

    def run(self) -> None:
        with self.ctx.session_factory() as session:
            clip = repo.get_clip(session, self.clip_id)
            if clip is None:
                return
            video = repo.get_video(session, clip.video_id)
            batch = repo.get_batch(session, clip.batch_id)
            if video is None or batch is None:
                return
            style = json.loads(batch.style_json)
            spec = ClipSpec(
                start_s=clip.start_s, end_s=clip.end_s, title=clip.title or "",
                show_title=bool(style.get("show_title", True)),
                style=clip.caption_style, crop=clip.crop_mode, blur=clip.blur,  # type: ignore[arg-type]
                words=clip_words(clip), blur_boxes=clip_blur_boxes(clip),
            )  # fmt: skip
            out = (
                self.ctx.data_dir / "previews" / f"{clip.id}.mp4"
                if self.preview
                else export_path(self.ctx, clip, video.title)
            )
            old_export = clip.export_path
            video_id = video.id
        video_dir = self.ctx.video_dir(video_id)
        info = (
            load_download(video_dir, self.ctx.config.ffmpeg_path)
            if (video_dir / SOURCE_FILE_NAME).is_file()
            else None
        )
        source = _source_video(video_dir, info)
        stages = ("cut", "face_crop", "captions", "render")

        def on_stage(name: str) -> None:
            stage = "preview" if self.preview and name == "render" else name
            self._update(status="running", stage=stage, progress=stages.index(name) / len(stages))

        with stage_timer("clip_preview" if self.preview else "clip_render", clip=self.clip_id):
            result = render_clip(
                source, self.ctx.clip_work_dir(self.clip_id), spec, out,
                "preview" if self.preview else "final",
                models_dir=self.ctx.data_dir / "models", ffmpeg_path=self.ctx.config.ffmpeg_path,
                memory=self.ctx.memory(), cancel=self.cancel, on_stage=on_stage,
            )  # fmt: skip
        thumbnail = make_thumbnail(
            result.output, self.ctx.data_dir / "thumbnails" / f"{self.clip_id}.jpg",
            ffmpeg_path=self.ctx.config.ffmpeg_path,
        )  # fmt: skip
        fields: dict[str, Any] = {
            "status": "done", "stage": None, "progress": 1.0,
            "thumbnail_path": self.ctx.relative(thumbnail),
            "error_code": None, "error_message": None,
        }  # fmt: skip
        if self.preview:
            fields["preview_path"] = self.ctx.relative(result.output)
        else:
            fields["export_path"] = self.ctx.relative(result.output)
            if old_export and old_export != fields["export_path"]:
                self.ctx.absolute(old_export).unlink(missing_ok=True)
        if result.crop.warning:
            fields["error_message"] = result.crop.warning
        self._update(**fields)
        apply_source_policy(self.ctx, video_id)


def _source_video(video_dir: Path, info: Any) -> SourceVideo:
    if info is not None:
        return SourceVideo(
            video_dir / SOURCE_FILE_NAME, info.probed_duration_s, info.width, info.height
        )
    # Source deleted (retention): the cut in work/ still has the handles.
    info_path = video_dir / "info.json"
    data = json.loads(info_path.read_text(encoding="utf-8")) if info_path.is_file() else {}
    return SourceVideo(
        None,
        float(data.get("probed_duration_s", 0.0)),
        int(data.get("width", 0)),
        int(data.get("height", 0)),
    )


def apply_source_policy(ctx: JobContext, video_id: str) -> None:
    """With `source_policy=delete`, remove `source.mp4` and `audio.wav` once every clip of the
    video has finished (docs/08 "Retention"); cut files in work/ are kept."""
    with ctx.session_factory() as session:
        if load_settings(session).source_policy != "delete":
            return
        clips = repo.clips_of_video(session, video_id)
        if not clips or any(c.status in ("queued", "running") for c in clips):
            return
    video_dir = ctx.video_dir(video_id)
    for name in (SOURCE_FILE_NAME, AUDIO_FILE_NAME):
        target = video_dir / name
        if target.is_file():
            target.unlink()
            logger.info("source policy delete: removed %s for video %s", name, video_id)
