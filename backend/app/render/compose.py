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
from app.render.layout import Layout, Panel, blur_radius

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
    # Gaming layouts (D-052): source rectangles of `fg_file` placed on the canvas; when set,
    # they replace the single scaled foreground.
    panels: Sequence[Panel] = ()


def write_ass(job: RenderJob) -> Path:
    path = job.work_dir / ASS_FILE_NAME
    title = censor_text(job.title) if job.title else job.title
    text = build_ass(censor_words(job.words), job.style, title, job.layout, job.duration_s)
    temp = path.with_suffix(".part")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)
    return path


def filter_graph(job: RenderJob, quality: Quality, base: bool = False) -> str:
    """The filter_complex for one render (input 0 = cut for background and audio, input 1 =
    the foreground source). With `base` (D-057) the layout is also split off before the blur
    boxes, title and captions into a 540x960 [b] for the editor's live preview."""
    small_w = lc.CANVAS_W // lc.BG_BLUR_DOWNSCALE
    small_h = lc.CANVAS_H // lc.BG_BLUR_DOWNSCALE
    radius = blur_radius(job.blur)
    blur = f",boxblur={radius}:{lc.BG_BLUR_POWER}" if radius > 0 else ""
    background = (
        f"[0:v]scale={lc.CANVAS_W}:{lc.CANVAS_H}:force_original_aspect_ratio=increase,"
        f"crop={lc.CANVAS_W}:{lc.CANVAS_H},scale={small_w}:{small_h}{blur},"
        f"scale={lc.CANVAS_W}:{lc.CANVAS_H},eq=brightness={lc.BG_BRIGHTNESS},setsar=1[bg]"
    )
    tail = f"ass={ASS_FILE_NAME}:fontsdir={WORK_FONTS_DIR_NAME}"
    if quality == "preview":
        tail += f",scale={lc.PREVIEW_W}:{lc.PREVIEW_H}"
    layers = foreground_layers(job)
    boxes = box_blur_chain(job.blur_boxes, "[base]", "[boxed]")
    finish = f"fps={lc.FPS},format=yuv420p"
    steps = [background, *layers]
    start = f"[over]{finish}"
    if base:
        steps += [
            f"[over]{finish},split[main][plain]",
            f"[plain]scale={lc.PREVIEW_W}:{lc.PREVIEW_H}[b]",
        ]
        start = "[main]null"
    if not boxes:
        return ";".join([*steps, f"{start},{tail}[v]"])
    return ";".join([*steps, f"{start}[base]", *boxes, f"[boxed]{tail}[v]"])


def foreground_layers(job: RenderJob) -> list[str]:
    """Filters that lay input 1 over [bg], ending in [over]: one scaled foreground, or the
    layout's panels (each a crop of input 1, scaled and placed)."""
    if not job.panels:
        return [
            f"[1:v]scale={job.layout.fg_w}:{job.layout.fg_h},setsar=1[fg]",
            f"[bg][fg]overlay=(W-w)/2:{job.layout.fg_top}[over]",
        ]
    count = len(job.panels)
    names = "".join(f"[src{i}]" for i in range(count))
    steps = [f"[1:v]split={count}{names}" if count > 1 else "[1:v]null[src0]"]
    current = "[bg]"
    for i, panel in enumerate(job.panels):
        src = panel.src
        out = "[over]" if i == count - 1 else f"[layer{i}]"
        steps.append(
            f"[src{i}]crop={src.w}:{src.h}:{src.x}:{src.y},"
            f"scale={panel.dest_w}:{panel.dest_h},setsar=1[panel{i}]"
        )
        steps.append(f"{current}[panel{i}]overlay=(W-w)/2:{panel.dest_y}{out}")
        current = out
    return steps


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
    starts at 0). Both mix inputs last exactly `duration_s` (the muted track is padded with
    silence, the beep has that duration), and amix uses `duration=longest`: with
    `duration=first`, FFmpeg 6.1 (Ubuntu 24.04) ended the mix 0.14 s early, so the clip's
    audio stopped before its video (measured on CI: 5.858 s instead of 6.000 s)."""
    on = "+".join(f"between(t,{start:.3f},{end:.3f})" for start, end in spans)
    muted = (
        f"[0:a:0]aresample={AUDIO_RATE},apad=whole_dur={duration_s:.3f},"
        f"volume=0:enable='{on}'[mute]"
    )
    beep = (
        f"sine=frequency={BEEP_HZ}:sample_rate={AUDIO_RATE}:duration={duration_s:.3f},"
        f"volume={BEEP_VOLUME},volume=0:enable='not({on})'[beep]"
    )
    mix = "[mute][beep]amix=inputs=2:duration=longest:normalize=0[a]"
    return ";".join([muted, beep, mix])


def render(
    job: RenderJob,
    out: Path,
    quality: Quality,
    *,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    cancel: threading.Event | None = None,
    base_out: Path | None = None,
) -> Path:
    """Render the clip to `out` (written to a temp name, then renamed). With `base_out`, the
    same ffmpeg run also writes the layout without blur boxes, title and captions there at
    540x960 (D-057, the editor's live preview).

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
    graph = filter_graph(job, quality, base=base_out is not None)
    audio_map = base_audio = "0:a:0?"
    if spans:
        graph += ";" + bleep_graph(spans, job.duration_s)
        audio_map = "[a]"
        if base_out is not None:
            graph += ";[a]asplit=2[a1][a2]"
            audio_map, base_audio = "[a1]", "[a2]"
        logger.info("bleeping %d bad-word spans", len(spans))
    crf, preset = (
        (lc.FINAL_CRF, lc.FINAL_PRESET)
        if quality == "final"
        else (lc.PREVIEW_CRF, lc.PREVIEW_PRESET)
    )
    temp = out.with_name(out.stem + ".part.mp4")
    base_temp = base_out.with_name(base_out.stem + ".part.mp4") if base_out else None
    extra: list[str] = []
    if base_out is not None and base_temp is not None:
        base_out.parent.mkdir(parents=True, exist_ok=True)
        extra = [
            "-map", "[b]", "-map", base_audio,
            "-c:v", "libx264", "-preset", lc.PREVIEW_PRESET, "-crf", str(lc.PREVIEW_CRF),
            "-pix_fmt", "yuv420p", "-r", str(lc.FPS), "-c:a", "aac", "-b:a", lc.AUDIO_BITRATE,
            "-movflags", "+faststart", "-t", f"{job.duration_s:.3f}", str(base_temp.resolve()),
        ]  # fmt: skip
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
        *extra,
    ]  # fmt: skip
    timeout = RENDER_TIMEOUT_BASE_S + job.duration_s * RENDER_TIMEOUT_PER_CLIP_S
    try:
        result = run_command(args, timeout, cancel, cwd=job.work_dir)
    except CommandError:
        _remove(temp, base_temp)
        raise
    if result.returncode != 0:
        _remove(temp, base_temp)
        logger.error("render failed: %s", result.stderr[-2000:])
        raise AppError(ErrorCode.RENDER_FAILED, "Rendering the clip failed.")
    temp.replace(out)
    if base_out is not None and base_temp is not None:
        try:
            base_temp.replace(base_out)
        except OSError as exc:
            # Windows refuses to replace a file the editor is streaming right now; the
            # preview itself is fine, only the live view stays off until the next preview.
            logger.warning("could not replace the live-preview base: %s", exc)
            base_temp.unlink(missing_ok=True)
            base_out.unlink(missing_ok=True)
    return out


def _remove(*paths: Path | None) -> None:
    for path in paths:
        if path is not None:
            path.unlink(missing_ok=True)


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
