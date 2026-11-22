"""Stages 11-12: write `captions.ass` and render the final 1080x1920 (or 540x960 preview) MP4
in one ffmpeg `filter_complex` pass (docs/05 "ffmpeg graph").

ffmpeg runs with its working directory set to the clip's work folder and relative names for
the ASS file and fonts folder, which avoids the Windows drive-colon/backslash escaping
problem in the `ass=` filter.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import (
    PROBE_TIMEOUT_S,
    RENDER_MIN_FREE_GB,
    RENDER_TIMEOUT_BASE_S,
    RENDER_TIMEOUT_PER_CLIP_S,
)
from app.core.errors import AppError, ErrorCode
from app.core.hardware import (
    MemoryInfo,
    disk_free_gb,
    ffmpeg_thread_args,
    find_ffprobe,
    require_ffmpeg,
)
from app.core.proc import CommandError, run_command
from app.pipeline.transcribe import Word
from app.render import layout_constants as lc
from app.render.ass import CaptionStyle, build_ass
from app.render.censor import BlurBox, bleep_spans, censor_text, censor_words
from app.render.fonts import WORK_FONTS_DIR_NAME, install_fonts
from app.render.layout import Layout, blur_radius

logger = logging.getLogger(__name__)

ASS_FILE_NAME = "captions.ass"
# Beep over bad words (D-048): a 1 kHz tone, quieter than full scale.
BEEP_HZ = 1000
BEEP_VOLUME = 0.25
AUDIO_RATE = 48000
# Manual blur boxes (D-048): the box is shrunk by this factor and scaled back up.
BOX_BLUR_SHRINK = 32
Quality = Literal["final", "preview"]


@dataclass(frozen=True)
class RenderJob:
    """Everything one render needs. Times are seconds inside `cut.mp4`."""

    work_dir: Path
    fg_file: str  # "crop.mp4" or "cut.mp4", relative to work_dir
    offset_s: float
    duration_s: float
    layout: Layout
    blur: int
    style: CaptionStyle
    words: Sequence[Word]  # relative to the clip start
    title: str | None
    blur_boxes: Sequence[BlurBox] = ()


def write_ass(job: RenderJob) -> Path:
    path = job.work_dir / ASS_FILE_NAME
    title = censor_text(job.title) if job.title else job.title
    text = build_ass(censor_words(job.words), job.style, title, job.layout, job.duration_s)
    temp = path.with_suffix(".part")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)
    return path


def filter_graph(job: RenderJob, quality: Quality) -> str:
    """The filter_complex for one render (input 0 = cut for background and audio, input 1 =
    the foreground source)."""
    small_w = lc.CANVAS_W // lc.BG_BLUR_DOWNSCALE
    small_h = lc.CANVAS_H // lc.BG_BLUR_DOWNSCALE
    radius = blur_radius(job.blur)
    blur = f",boxblur={radius}:{lc.BG_BLUR_POWER}" if radius > 0 else ""
    background = (
        f"[0:v]scale={lc.CANVAS_W}:{lc.CANVAS_H}:force_original_aspect_ratio=increase,"
        f"crop={lc.CANVAS_W}:{lc.CANVAS_H},scale={small_w}:{small_h}{blur},"
        f"scale={lc.CANVAS_W}:{lc.CANVAS_H},eq=brightness={lc.BG_BRIGHTNESS},setsar=1[bg]"
    )
    foreground = f"[1:v]scale={job.layout.fg_w}:{job.layout.fg_h},setsar=1[fg]"
    tail = f"ass={ASS_FILE_NAME}:fontsdir={WORK_FONTS_DIR_NAME}"
    if quality == "preview":
        tail += f",scale={lc.PREVIEW_W}:{lc.PREVIEW_H}"
    boxes = box_blur_chain(job.blur_boxes, "[base]", "[boxed]")
    if not boxes:
        compose = (
            f"[bg][fg]overlay=(W-w)/2:{job.layout.fg_top},fps={lc.FPS},format=yuv420p,{tail}[v]"
        )
        return ";".join([background, foreground, compose])
    compose = f"[bg][fg]overlay=(W-w)/2:{job.layout.fg_top},fps={lc.FPS},format=yuv420p[base]"
    return ";".join([background, foreground, compose, *boxes, f"[boxed]{tail}[v]"])


def _even(value: float) -> int:
    return max(2, round(value / 2) * 2)


def box_blur_chain(boxes: Sequence[BlurBox], first: str, last: str) -> list[str]:
    """Filters that blur each box of the canvas during its time (captions and title are drawn
    after, so they stay readable). Empty when there are no boxes."""
    steps: list[str] = []
    current = first
    for number, box in enumerate(boxes):
        x, y = _even(box.x * lc.CANVAS_W), _even(box.y * lc.CANVAS_H)
        w = min(_even(box.w * lc.CANVAS_W), lc.CANVAS_W - x)
        h = min(_even(box.h * lc.CANVAS_H), lc.CANVAS_H - y)
        if w < 2 or h < 2:
            continue
        small_w, small_h = max(2, w // BOX_BLUR_SHRINK), max(2, h // BOX_BLUR_SHRINK)
        out = last if number == len(boxes) - 1 else f"[box{number}]"
        steps.append(
            f"{current}split[keep{number}][take{number}];"
            f"[take{number}]crop={w}:{h}:{x}:{y},scale={small_w}:{small_h}:flags=area,"
            f"scale={w}:{h}:flags=bilinear,boxblur=4:1[blur{number}];"
            f"[keep{number}][blur{number}]overlay={x}:{y}:"
            f"enable='between(t,{box.start:.3f},{box.end:.3f})'{out}"
        )
        current = out
    if steps and current != last:
        steps.append(f"{current}null{last}")
    return steps


def bleep_graph(spans: list[tuple[float, float]], duration_s: float) -> str:
    """Audio filters that mute input 0's audio during `spans` and lay a beep over them,
    output label [a]. Times are seconds from the clip start (input 0 is seeked, so t
    starts at 0)."""
    on = "+".join(f"between(t,{start:.3f},{end:.3f})" for start, end in spans)
    muted = f"[0:a:0]aresample={AUDIO_RATE},volume=0:enable='{on}'[mute]"
    beep = (
        f"sine=frequency={BEEP_HZ}:sample_rate={AUDIO_RATE}:duration={duration_s:.3f},"
        f"volume={BEEP_VOLUME},volume=0:enable='not({on})'[beep]"
    )
    mix = "[mute][beep]amix=inputs=2:duration=first:normalize=0[a]"
    return ";".join([muted, beep, mix])


def render(
    job: RenderJob,
    out: Path,
    quality: Quality,
    *,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    cancel: threading.Event | None = None,
) -> Path:
    """Render the clip to `out` (written to a temp name, then renamed).

    Raises:
        AppError: FFMPEG_MISSING, DISK_FULL, or RENDER_FAILED.
        CommandCancelledError / CommandTimeoutError.
    """
    ffmpeg = require_ffmpeg(ffmpeg_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    free = disk_free_gb(out.parent)
    if free < RENDER_MIN_FREE_GB:
        raise AppError(
            ErrorCode.DISK_FULL,
            "Not enough free disk space to render the clip.",
            {"free_gb": round(free, 2), "needed_gb": RENDER_MIN_FREE_GB},
        )
    install_fonts(job.work_dir)
    write_ass(job)
    spans = bleep_spans(job.words, job.duration_s)
    if spans and probe_streams(job.work_dir / "cut.mp4", ffmpeg_path).audio_s is None:
        spans = []
    graph = filter_graph(job, quality)
    audio_map = "0:a:0?"
    if spans:
        graph += ";" + bleep_graph(spans, job.duration_s)
        audio_map = "[a]"
        logger.info("bleeping %d bad-word spans", len(spans))
    crf, preset = (
        (lc.FINAL_CRF, lc.FINAL_PRESET)
        if quality == "final"
        else (lc.PREVIEW_CRF, lc.PREVIEW_PRESET)
    )
    temp = out.with_name(out.stem + ".part.mp4")
    seek = ["-ss", f"{job.offset_s:.3f}", "-t", f"{job.duration_s:.3f}"]
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
        *ffmpeg_thread_args(memory),
        *seek, "-i", "cut.mp4", *seek, "-i", job.fg_file,
        "-filter_complex", graph,
        "-map", "[v]", "-map", audio_map,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-r", str(lc.FPS), "-c:a", "aac", "-b:a", lc.AUDIO_BITRATE,
        "-movflags", "+faststart", "-t", f"{job.duration_s:.3f}", str(temp.resolve()),
    ]  # fmt: skip
    timeout = RENDER_TIMEOUT_BASE_S + job.duration_s * RENDER_TIMEOUT_PER_CLIP_S
    try:
        result = run_command(args, timeout, cancel, cwd=job.work_dir)
    except CommandError:
        temp.unlink(missing_ok=True)
        raise
    if result.returncode != 0:
        temp.unlink(missing_ok=True)
        logger.error("render failed: %s", result.stderr[-2000:])
        raise AppError(ErrorCode.RENDER_FAILED, "Rendering the clip failed.")
    temp.replace(out)
    return out


def make_thumbnail(
    video: Path, out: Path, *, ffmpeg_path: str | None, at_s: float = lc.THUMBNAIL_AT_S
) -> Path:
    """A small JPEG frame of a rendered clip.

    Raises:
        AppError RENDER_FAILED.
    """
    ffmpeg = require_ffmpeg(ffmpeg_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_name(out.stem + ".part.jpg")
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
        "-ss", f"{at_s:.3f}", "-i", str(video), "-frames:v", "1",
        "-vf", f"scale={lc.THUMBNAIL_W}:-2", "-q:v", str(lc.THUMBNAIL_QUALITY), str(temp),
    ]  # fmt: skip
    result = run_command(args, PROBE_TIMEOUT_S * 4)
    if result.returncode != 0 or not temp.is_file():
        temp.unlink(missing_ok=True)
        logger.error("thumbnail failed: %s", result.stderr[-1000:])
        raise AppError(ErrorCode.RENDER_FAILED, "Making the thumbnail failed.")
    temp.replace(out)
    return out


@dataclass(frozen=True)
class StreamDurations:
    video_s: float
    audio_s: float | None
    width: int
    height: int
    fps: str


def probe_streams(path: Path, ffmpeg_path: str | None) -> StreamDurations:
    """Video/audio stream durations and video size (for A/V sync checks).

    Raises:
        AppError FFMPEG_MISSING / RENDER_FAILED.
    """
    ffprobe = find_ffprobe(ffmpeg_path)
    if ffprobe is None:
        raise AppError(ErrorCode.FFMPEG_MISSING, "ffprobe was not found.")
    args = [
        ffprobe, "-v", "error", "-show_entries",
        "stream=codec_type,duration,width,height,r_frame_rate", "-of", "json", str(path),
    ]  # fmt: skip
    result = run_command(args, PROBE_TIMEOUT_S)
    try:
        streams = json.loads(result.stdout)["streams"]
        video = next(s for s in streams if s["codec_type"] == "video")
        audio = next((s for s in streams if s["codec_type"] == "audio"), None)
        return StreamDurations(
            video_s=float(video["duration"]),
            audio_s=float(audio["duration"]) if audio else None,
            width=int(video["width"]),
            height=int(video["height"]),
            fps=str(video["r_frame_rate"]),
        )
    except (json.JSONDecodeError, KeyError, StopIteration, ValueError) as exc:
        raise AppError(ErrorCode.RENDER_FAILED, "The rendered file cannot be read.") from exc
