"""Close analysis of draft clips for the second selection round (D-046).

Round 1 drafts about twice the clips wanted. Each draft is analysed from 20 s before its start
to 20 s after its end, so the LLM can see what happens just outside it (a kill 3 s after the
cut, a new round 5 s into it):
- what is on screen every 2 s (CLIP, the screen type model of D-040, on the zone only);
- the sound events every 2 s (D-041, already computed for the whole video);
- on-screen text every 4 s (RapidOCR, D-045; cached with the candidate frames);
- the transcript, by its times;
- scene ends: a death screen, scoreboard or menu, round words on screen ("BUY PHASE",
  "KILLED BY", "VICTORY", ...) or in the speech ("new game", "next round");
- a few small frames (start, a third, two thirds, end, and one after the end) for models that
  accept images.

Every part is optional: what cannot be read is left out of the description.
"""

from __future__ import annotations

import logging
import math
import re
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from app.config import (
    KILL_SPEECH,
    KILL_TEXT,
    OCR_MIN_CONFIDENCE,
    OCR_WIDTH,
    PROBE_TIMEOUT_S,
    REVIEW_AFTER_FRAME_S,
    REVIEW_DULL_MIN,
    REVIEW_DULL_ROWS,
    REVIEW_FRAME_SHARES,
    REVIEW_FRAME_WIDTH,
    REVIEW_JPEG_QUALITY,
    REVIEW_OCR_MAX,
    REVIEW_OCR_STEP_S,
    REVIEW_SOUND_MIN,
    REVIEW_STEP_S,
    REVIEW_ZONE_S,
    SCENE_END_SPEECH,
    SCENE_END_TEXT,
    SCREEN_DULL_GROUPS,
    SCREEN_IMAGE_SIZE,
)
from app.core.hardware import find_ffprobe, require_ffmpeg
from app.core.proc import CommandCancelledError
from app.llm.prompts import clock
from app.pipeline import screen as screen_module
from app.pipeline.screen_text import (
    ScreenText,
    is_overlay,
    keep_text,
    make_reader,
    overlay_texts,
)
from app.pipeline.sound import EVENT_NAMES, SoundEvents
from app.pipeline.transcribe import Word
from app.sources.base import SOURCE_FILE_NAME

logger = logging.getLogger(__name__)

_SCENE_TEXT = re.compile(SCENE_END_TEXT)
_SCENE_SPEECH = re.compile(SCENE_END_SPEECH)
_FRAME_BYTES = SCREEN_IMAGE_SIZE * SCREEN_IMAGE_SIZE * 3
Image = np.ndarray[Any, np.dtype[np.uint8]]
# Shown for each screen group in the timeline.
SCREEN_WORDS = {
    "gameplay": "gameplay",
    "menu": "menu/death screen/scoreboard",
    "waiting": "black screen",
    "talking": "people talking",
    "other": "other",
}

# (start, length) of the zone -> one group name and its dull share per REVIEW_STEP_S.
ScreenReader = Callable[[float, float], list[tuple[str, float]]]
# Frame times -> on-screen text by time key ("12" -> ["KILLED BY HUSH"]).
TextReader = Callable[[Sequence[float]], ScreenText | None]
# Frame time -> JPEG bytes, or None.
FrameGrabber = Callable[[float], bytes | None]


@dataclass
class Row:
    """What happens in [t, t + REVIEW_STEP_S)."""

    t: float
    screen: str = ""
    dull: float = 0.0
    sounds: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    speech: str = ""


@dataclass
class ClipAnalysis:
    """One draft clip, analysed over its zone (D-046)."""

    id: int
    start: float
    end: float
    zone_start: float
    zone_end: float
    rows: list[Row]
    # (time, why) of each scene end found in the zone.
    scene_ends: list[tuple[float, str]]
    # Times with action (sound events, kill text).
    actions: list[float]
    # (label, JPEG) for the LLM: "start", "1/3", "2/3", "end", "after end".
    frames: list[tuple[str, bytes]] = field(default_factory=list)
    # (time, what) of kills said or shown in the zone: "said \"I catch all three\"".
    kills: list[tuple[float, str]] = field(default_factory=list)

    def summary(self) -> list[str]:
        """Plain-language findings the LLM should not miss."""
        notes: list[str] = []
        inside = [t for t in self.actions if self.start <= t < self.end]
        after = [t for t in self.actions if t >= self.end]
        before = [t for t in self.actions if t < self.start]
        if not inside:
            notes.append("No action (fight sounds or kill text) inside the clip.")
        else:
            lead = inside[0] - self.start
            if lead > 8:
                notes.append(f"The first action is {lead:.0f} s after the clip starts.")
        if after:
            notes.append(f"Action continues {after[0] - self.end:.0f} s after the clip ends.")
        if before and not inside:
            notes.append(f"Action ended {self.start - before[-1]:.0f} s before the clip starts.")
        crossing = [t for t, _ in self.scene_ends if self.start + 3 < t < self.end - 3]
        for t in crossing:
            notes.append(f"A round or scene ends at {clock(t)}, inside the clip.")
        kills = [f"{what} at {clock(t)}" for t, what in self.kills if self.start <= t < self.end]
        if kills:
            notes.append("Kill evidence inside the clip: " + "; ".join(kills[:4]) + ".")
        else:
            notes.append("No kill evidence inside the clip.")
        outside = [f"{what} at {clock(t)}" for t, what in self.kills
                   if not self.start <= t < self.end]  # fmt: skip
        if outside:
            notes.append("Kill evidence just outside the clip: " + "; ".join(outside[:3]) + ".")
        return notes


# ----- building the analysis (pure, unit-tested) -----


def zone_of(start: float, end: float, duration_s: float) -> tuple[float, float]:
    """The analysed range, starting on a whole second so the 2 s screen rows and the 4 s
    text frames fall on the same frames (one grab each)."""
    return float(max(0, math.floor(start - REVIEW_ZONE_S))), min(duration_s, end + REVIEW_ZONE_S)


def ocr_times(zone_start: float, zone_end: float) -> list[float]:
    """Every REVIEW_OCR_STEP_S in the zone, evenly thinned to at most REVIEW_OCR_MAX."""
    times = [float(t) for t in np.arange(round(zone_start), zone_end, REVIEW_OCR_STEP_S)]
    if len(times) <= REVIEW_OCR_MAX:
        return times
    keep = np.linspace(0, len(times) - 1, num=REVIEW_OCR_MAX).round().astype(int)
    return [times[i] for i in keep]


def _sounds_at(events: SoundEvents | None, t: float) -> list[str]:
    if events is None:
        return []
    index = int(t // events.window_s)
    if not 0 <= index < len(events.windows):
        return []
    groups = events.windows[index]
    return [
        EVENT_NAMES[group]
        for group, value in sorted(groups.items(), key=lambda pair: -pair[1])
        if group in EVENT_NAMES and value >= REVIEW_SOUND_MIN
    ]


def build_rows(
    zone_start: float,
    zone_end: float,
    screens: Sequence[tuple[str, float]],
    events: SoundEvents | None,
    text: ScreenText | None,
    words: Sequence[Word],
) -> list[Row]:
    """One row per REVIEW_STEP_S of the zone."""
    overlay = overlay_texts(text.frames) if text is not None else set()
    rows: list[Row] = []
    for index, t in enumerate(np.arange(zone_start, zone_end, REVIEW_STEP_S)):
        row = Row(t=float(t))
        if index < len(screens):
            row.screen, row.dull = screens[index]
        row.sounds = _sounds_at(events, float(t))
        if text is not None:
            for key, texts in text.frames.items():
                if t <= float(key) < t + REVIEW_STEP_S:
                    row.text += [item for item in texts if not is_overlay(item, overlay)]
        row.speech = " ".join(w.text for w in words if t <= w.start < t + REVIEW_STEP_S)
        rows.append(row)
    return rows


def scene_ends(rows: Sequence[Row]) -> list[tuple[float, str]]:
    """Where a round or scene ends: a dull screen after a non-dull one, round words on screen,
    or a spoken round marker. Ends closer than 2 steps to the previous one are merged."""
    found: list[tuple[float, str]] = []
    run = 0
    for row in rows:
        reason = ""
        run = run + 1 if row.dull >= REVIEW_DULL_MIN else 0
        if run == REVIEW_DULL_ROWS:
            # The scene ended where the dull run began.
            row = Row(t=row.t - (REVIEW_DULL_ROWS - 1) * REVIEW_STEP_S, text=row.text,
                      speech=row.speech)  # fmt: skip
            reason = "menu/death screen/scoreboard"
        on_screen = _SCENE_TEXT.search(" ".join(row.text).lower())
        if on_screen is not None:
            reason = f'"{on_screen.group(0).upper()}" on screen'
        spoken = _SCENE_SPEECH.search(row.speech.lower())
        if spoken is not None:
            reason = f'said "{spoken.group(0)}"'
        if reason and (not found or row.t - found[-1][0] >= 2 * REVIEW_STEP_S):
            found.append((row.t, reason))
    return found


_KILL_TEXT = re.compile(r"kill|eliminat|headshot|ace|clutch|planted|defused|victory|wasted")
_KILLS_SAID = re.compile(KILL_SPEECH)
_KILLS_SHOWN = re.compile(KILL_TEXT)


def kill_evidence(rows: Sequence[Row]) -> list[tuple[float, str]]:
    """Kills said ("I catch all three") or shown ("KILLED BY", "ELIMINATED") per row."""
    found: list[tuple[float, str]] = []
    for row in rows:
        said = _KILLS_SAID.search(row.speech.lower())
        if said is not None:
            found.append((row.t, f'said "{_phrase(row.speech, said.start(), said.end())}"'))
            continue
        shown = next((t for t in row.text if _KILLS_SHOWN.search(t.lower())), None)
        if shown is not None:
            found.append((row.t, f'"{shown}" on screen'))
    return found


def _phrase(text: str, start: int, end: int, around: int = 20) -> str:
    """The matched words with a little context, cut at word edges."""
    left = text.rfind(" ", 0, max(0, start - around)) + 1
    right = text.find(" ", min(len(text), end + around))
    return text[left : right if right != -1 else len(text)].strip()


def actions(rows: Sequence[Row]) -> list[float]:
    return [
        row.t for row in rows if row.sounds or any(_KILL_TEXT.search(t.lower()) for t in row.text)
    ]


def _stamp(t: float) -> str:
    """ "[24:30 | 1470 s]": the clock for reading, the seconds to answer with (a real
    Gemini answer once gave times the backend could not place, D-046)."""
    return f"[{clock(t)} | {t:.0f} s]"


def describe(analysis: ClipAnalysis) -> str:
    """The timeline for the prompt: every row with something in it, and the clip's start and
    end marked. Rows outside the clip say so."""
    lines = [
        f"Clip {analysis.id}: now {clock(analysis.start)}-{clock(analysis.end)} "
        f"({analysis.end - analysis.start:.0f} s); analysed "
        f"{clock(analysis.zone_start)}-{clock(analysis.zone_end)} "
        f"({analysis.zone_start:.1f}-{analysis.zone_end:.1f} s)"
    ]
    lines += [f"Note: {note}" for note in analysis.summary()]
    marked_start = marked_end = False
    scene_times = {t: why for t, why in analysis.scene_ends}
    for row in analysis.rows:
        if not marked_start and row.t >= analysis.start - 1e-6:
            lines.append(f"{_stamp(analysis.start)} ---- CLIP START ----")
            marked_start = True
        if not marked_end and row.t >= analysis.end - 1e-6:
            lines.append(f"{_stamp(analysis.end)} ---- CLIP END ----")
            marked_end = True
        parts: list[str] = []
        if row.screen and row.screen != "gameplay":
            parts.append(SCREEN_WORDS.get(row.screen, row.screen))
        if row.sounds:
            parts.append(", ".join(row.sounds))
        if row.text:
            parts.append("text " + ", ".join(f'"{t}"' for t in row.text[:6]))
        if row.speech:
            parts.append(f'says "{row.speech}"')
        if row.t in scene_times:
            parts.append(f"SCENE ENDS ({scene_times[row.t]})")
        if parts:
            lines.append(f"{_stamp(row.t)} " + " | ".join(parts))
    if not marked_end:
        lines.append(f"{_stamp(analysis.end)} ---- CLIP END ----")
    return "\n".join(lines)


# ----- reading the video (I/O) -----


def frame_times_for(start: float, end: float, duration_s: float) -> list[tuple[str, float]]:
    labels = ("start", "1/3", "2/3", "end")
    times = [
        (label, min(duration_s - 0.5, start + share * (end - start) + (0.5 if share == 0 else 0)))
        for label, share in zip(labels, REVIEW_FRAME_SHARES, strict=True)
    ]
    after = end + REVIEW_AFTER_FRAME_S
    if after < duration_s:
        times.append(("after end", after))
    return times


@dataclass
class VideoReaders:
    """The readers one video's review uses; replaceable in tests."""

    screens: ScreenReader | None
    text: TextReader | None
    frame: FrameGrabber | None


def analyse(
    clip_id: int,
    start: float,
    end: float,
    duration_s: float,
    *,
    events: SoundEvents | None,
    words: Sequence[Word],
    readers: VideoReaders,
) -> ClipAnalysis:
    zone_start, zone_end = zone_of(start, end, duration_s)
    screens = readers.screens(zone_start, zone_end - zone_start) if readers.screens else []
    text = readers.text(ocr_times(zone_start, zone_end)) if readers.text else None
    rows = build_rows(zone_start, zone_end, screens, events, text, words)
    frames: list[tuple[str, bytes]] = []
    if readers.frame is not None:
        for label, t in frame_times_for(start, end, duration_s):
            image = readers.frame(t)
            if image is not None:
                frames.append((label, image))
    return ClipAnalysis(
        id=clip_id, start=start, end=end, zone_start=zone_start, zone_end=zone_end,
        rows=rows, scene_ends=scene_ends(rows), actions=actions(rows), frames=frames,
        kills=kill_evidence(rows),
    )  # fmt: skip


def video_readers(
    video_dir: Path,
    models_dir: Path,
    ffmpeg_path: str | None,
    cancel: threading.Event | None = None,
) -> VideoReaders:
    """Readers on the real video. A reader that cannot start (model missing, package
    missing) is left out and logged; the analysis then goes without it."""
    source = video_dir / SOURCE_FILE_NAME
    ffmpeg = require_ffmpeg(ffmpeg_path)
    width, height = _frame_size(source, ffmpeg_path)
    # Per thread (drafts are analysed in parallel): the keyframes of the current draft's zone
    # (one ffmpeg pass, decoding keyframes only: 0.6 s for 65 s of the owner's 1080p60 video
    # against 10 s for 33 exact seeks), shared by the screen and text readers, and the
    # thread's own OCR reader. Keyframes there are 4-6 s apart; each row uses the nearest.
    local = threading.local()

    def load_zone(zone_start: float, length: float) -> None:
        local.frames = zone_keyframes(ffmpeg, source, zone_start, length, width, height)
        local.zone = (zone_start, zone_start + length)

    def frame_at(t: float) -> Image | None:
        frames: list[tuple[float, Image]] = getattr(local, "frames", [])
        if not frames:
            return None
        return min(frames, key=lambda pair: abs(pair[0] - t))[1]

    screens: ScreenReader | None = None
    try:
        session = screen_module._session(screen_module.ensure_model(models_dir, cancel))
        labels = screen_module.load_labels()
        dull_index = [i for i, g in enumerate(labels.groups) if g in SCREEN_DULL_GROUPS]

        def read_screens(zone_start: float, length: float) -> list[tuple[str, float]]:
            import cv2  # from opencv-contrib-python

            if cancel is not None and cancel.is_set():
                raise CommandCancelledError("review was cancelled")
            load_zone(zone_start, length)
            frames: list[tuple[float, Image]] = local.frames
            if not frames:
                return []
            size = (SCREEN_IMAGE_SIZE, SCREEN_IMAGE_SIZE)
            inputs = [
                screen_module.preprocess(
                    cv2.cvtColor(cv2.resize(image, size, interpolation=cv2.INTER_AREA),
                                 cv2.COLOR_BGR2RGB).tobytes()
                )
                for _, image in frames
            ]  # fmt: skip
            name = session.get_inputs()[0].name
            embeddings = session.run(None, {name: np.stack(inputs)})[0]
            shares = screen_module.group_shares(np.asarray(embeddings, dtype=np.float32), labels)
            per_frame = [
                (labels.groups[int(row.argmax())], float(row[dull_index].sum())) for row in shares
            ]
            times = [t for t, _ in frames]
            rows = np.arange(zone_start, zone_start + length, REVIEW_STEP_S)
            return [per_frame[_nearest(times, float(t))] for t in rows]

        screens = read_screens
    except Exception as exc:  # noqa: BLE001 - optional reader, logged; onnxruntime errors are plain
        logger.warning("review screen reader off: %s", exc)

    def read_text(times: Sequence[float]) -> ScreenText | None:
        """On-screen text of the frames (kept in memory: the result lives in selection.json,
        and parallel drafts must not race on the cache file)."""
        try:
            zone = getattr(local, "zone", None)
            if times and (zone is None or not zone[0] <= times[0] < zone[1]):
                # No screen reader ran for this draft: load its zone here.
                load_zone(times[0], times[-1] - times[0] + REVIEW_OCR_STEP_S)
            if not hasattr(local, "reader"):
                local.reader = make_reader()
            # Every keyframe of the zone, each under its own time (round banners last 2-4 s,
            # so a thinned set missed "BUY PHASE" on the owner's video).
            keyframes: list[tuple[float, Image]] = getattr(local, "frames", [])
            if len(keyframes) > REVIEW_OCR_MAX:
                keep = np.linspace(0, len(keyframes) - 1, num=REVIEW_OCR_MAX).round().astype(int)
                keyframes = [keyframes[i] for i in sorted(set(keep.tolist()))]
            frames: dict[str, list[str]] = {}
            for t, image in keyframes:
                if cancel is not None and cancel.is_set():
                    raise CommandCancelledError("review was cancelled")
                frames[f"{t:.2f}"] = [
                    text.strip() for text, score in local.reader(image)
                    if score >= OCR_MIN_CONFIDENCE and keep_text(text)
                ]  # fmt: skip
            return ScreenText(frames=frames)
        except CommandCancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - optional reader, logged
            logger.warning("review text reader failed: %s", exc)
            return None

    def grab(t: float) -> bytes | None:
        raw = _run(
            [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-ss",
                f"{t:.3f}",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-an",
                "-vf",
                f"scale={REVIEW_FRAME_WIDTH}:-2",
                "-q:v",
                str(REVIEW_JPEG_QUALITY),
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "pipe:1",
            ]
        )
        return raw or None

    return VideoReaders(screens=screens, text=read_text, frame=grab)


def _nearest(times: Sequence[float], t: float) -> int:
    return min(range(len(times)), key=lambda i: abs(times[i] - t))


def _frame_size(source: Path, ffmpeg_path: str | None) -> tuple[int, int]:
    """(width, height) of the review frames: 960 px wide (OCR_WIDTH), even height."""
    ffprobe = find_ffprobe(ffmpeg_path)
    width, height = 1920, 1080
    if ffprobe is not None:
        out = _run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height", "-of", "csv=p=0", str(source)]
        ).decode("ascii", "ignore").strip().split(",")  # fmt: skip
        if len(out) == 2 and out[0].isdigit() and out[1].isdigit():
            width, height = int(out[0]), int(out[1])
    return OCR_WIDTH, max(2, round(OCR_WIDTH * height / width / 2) * 2)


_PTS = re.compile(rb"pts_time:(-?[0-9.]+)")


def zone_keyframes(
    ffmpeg: str, source: Path, zone_start: float, length: float, width: int, height: int
) -> list[tuple[float, Image]]:
    """The keyframes in [zone_start, zone_start + length) as (time, BGR image): one ffmpeg
    pass that decodes keyframes only; times come from `showinfo` in output order."""
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "info", "-skip_frame", "nokey",
        "-ss", f"{zone_start:.3f}", "-t", f"{length:.3f}", "-i", str(source), "-an",
        "-vf", f"showinfo,scale={width}:{height}", "-fps_mode", "passthrough",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
    ]  # fmt: skip
    try:
        result = subprocess.run(  # noqa: S603 - fixed ffmpeg command, no shell
            args, capture_output=True, timeout=PROBE_TIMEOUT_S * 4, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("review keyframes failed: %s", exc)
        return []
    if result.returncode != 0:
        return []
    size = width * height * 3
    times = [zone_start + float(m) for m in _PTS.findall(result.stderr)]
    count = min(len(result.stdout) // size, len(times))
    return [
        (times[i], np.frombuffer(result.stdout[i * size : (i + 1) * size], dtype=np.uint8)
         .reshape(height, width, 3))
        for i in range(count)
    ]  # fmt: skip


def _run(args: list[str]) -> bytes:
    """stdout of a short ffmpeg command; empty on failure (logged)."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed ffmpeg command, no shell
            args, capture_output=True, timeout=PROBE_TIMEOUT_S * 4, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("review ffmpeg failed: %s", exc)
        return b""
    return result.stdout if result.returncode == 0 else b""
