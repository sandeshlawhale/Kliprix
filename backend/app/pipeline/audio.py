"""Stage 4, audio: `audio.wav` (16 kHz mono) and `signals/audio.json` (docs/04, 08).

The per-second value is the mean of two robust z-scores: loudness (RMS in dB) and spectral
flux (librosa onset strength). The WAV is read in `AUDIO_CHUNK_S` chunks, never whole.
"""

from __future__ import annotations

import logging
import threading
import wave
from collections.abc import Callable
from pathlib import Path

import librosa
import numpy as np
import numpy.typing as npt

from app.config import (
    AUDIO_CHUNK_S,
    AUDIO_FLUX_HOP,
    AUDIO_RMS_FRAME,
    AUDIO_RMS_HOP,
    AUDIO_SAMPLE_RATE,
    PROBE_TIMEOUT_S,
    SIGNAL_TIMEOUT_BASE_S,
    SIGNAL_TIMEOUT_PER_VIDEO_S,
)
from app.core.errors import AppError, ErrorCode
from app.core.hardware import MemoryInfo, ffmpeg_thread_args, find_ffprobe, require_ffmpeg
from app.core.proc import CommandCancelledError, CommandError, run_command
from app.pipeline.scoring import fit_length, robust_z
from app.pipeline.signals import SignalFile, grid_length, load_signal, save_signal
from app.sources.base import SOURCE_FILE_NAME

logger = logging.getLogger(__name__)

AUDIO_FILE_NAME = "audio.wav"
# Floor for RMS before taking the logarithm (silence would be -inf dB).
RMS_FLOOR = 1e-5
# 16-bit PCM full scale.
PCM16_SCALE = 32768.0
# A cached WAV must be within this many seconds of the video length.
WAV_TOLERANCE_S = 2.0


def has_audio_stream(path: Path, ffmpeg_path: str | None) -> bool:
    """Whether the file has at least one audio stream.

    Raises:
        AppError: FFMPEG_MISSING when ffprobe is not available.
    """
    ffprobe = find_ffprobe(ffmpeg_path)
    if ffprobe is None:
        raise AppError(
            ErrorCode.FFMPEG_MISSING,
            "ffprobe was not found. Install ffmpeg and run the system check again.",
        )
    args = [
        ffprobe, "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(path),
    ]  # fmt: skip
    result = run_command(args, PROBE_TIMEOUT_S)
    return result.returncode == 0 and bool(result.stdout.strip())


def wav_duration_s(path: Path) -> float | None:
    """Length of a 16 kHz mono 16-bit WAV, or None when it is not one."""
    try:
        with wave.open(str(path), "rb") as reader:
            if (
                reader.getframerate() != AUDIO_SAMPLE_RATE
                or reader.getnchannels() != 1
                or reader.getsampwidth() != 2
            ):
                return None
            return reader.getnframes() / AUDIO_SAMPLE_RATE
    except (OSError, EOFError, wave.Error):
        return None


def ensure_audio_wav(
    video_dir: Path,
    duration_s: float,
    *,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    cancel: threading.Event | None = None,
) -> Path:
    """Extract `audio.wav` (16 kHz mono PCM) from `source.mp4` unless a valid one exists.

    A source without an audio track gives a silent WAV of the same length.

    Raises:
        AppError: FFMPEG_MISSING, or INTERNAL when ffmpeg fails.
        CommandCancelledError: `cancel` was set.
    """
    wav = video_dir / AUDIO_FILE_NAME
    existing = wav_duration_s(wav) if wav.is_file() else None
    if existing is not None and abs(existing - duration_s) <= WAV_TOLERANCE_S:
        return wav
    ffmpeg = require_ffmpeg(ffmpeg_path)
    temp = wav.with_name(wav.stem + ".part.wav")
    source = video_dir / SOURCE_FILE_NAME
    if has_audio_stream(source, ffmpeg_path):
        inputs = ["-i", str(source), "-vn"]
    else:
        # No audio track: a silent WAV of the same length keeps later stages uniform.
        inputs = ["-f", "lavfi", "-i", f"anullsrc=r={AUDIO_SAMPLE_RATE}:cl=mono"]
    args = [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
        *ffmpeg_thread_args(memory), *inputs,
        "-ac", "1", "-ar", str(AUDIO_SAMPLE_RATE), "-c:a", "pcm_s16le",
        "-t", f"{duration_s:.3f}", str(temp),
    ]  # fmt: skip
    timeout = SIGNAL_TIMEOUT_BASE_S + duration_s * SIGNAL_TIMEOUT_PER_VIDEO_S
    try:
        result = run_command(args, timeout, cancel)
    except CommandError:
        temp.unlink(missing_ok=True)
        raise
    if result.returncode != 0:
        temp.unlink(missing_ok=True)
        logger.error("audio extract failed: %s", result.stderr[-2000:])
        raise AppError(ErrorCode.INTERNAL, "Could not read the audio track of the video.")
    temp.replace(wav)
    return wav


def _per_second_mean(
    values: npt.NDArray[np.float64], seconds: npt.NDArray[np.int64], count: int
) -> npt.NDArray[np.float64]:
    sums = np.bincount(seconds, weights=values, minlength=count)[:count]
    counts = np.bincount(seconds, minlength=count)[:count]
    return np.divide(sums, counts, out=np.zeros(count), where=counts > 0)


def chunk_features(samples: npt.NDArray[np.float32]) -> tuple[list[float], list[float]]:
    """Per-second RMS (dB) and spectral flux for a chunk that is a whole number of seconds."""
    seconds = len(samples) // AUDIO_SAMPLE_RATE
    rms = librosa.feature.rms(
        y=samples, frame_length=AUDIO_RMS_FRAME, hop_length=AUDIO_RMS_HOP, center=False
    )[0]
    rms_db = 20.0 * np.log10(np.maximum(rms.astype(np.float64), RMS_FLOOR))
    rms_seconds = (np.arange(len(rms_db)) * AUDIO_RMS_HOP) // AUDIO_SAMPLE_RATE
    flux = librosa.onset.onset_strength(
        y=samples, sr=AUDIO_SAMPLE_RATE, hop_length=AUDIO_FLUX_HOP
    ).astype(np.float64)
    flux_seconds = (np.arange(len(flux)) * AUDIO_FLUX_HOP) // AUDIO_SAMPLE_RATE
    return (
        _per_second_mean(rms_db, rms_seconds.astype(np.int64), seconds).tolist(),
        _per_second_mean(flux, flux_seconds.astype(np.int64), seconds).tolist(),
    )


def audio_series(
    wav: Path,
    duration_s: float,
    cancel: threading.Event | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> list[float]:
    """The per-second audio signal for `wav`, read chunk by chunk; `on_progress` gets the
    fraction of the file analysed so far."""
    rms_all: list[float] = []
    flux_all: list[float] = []
    chunk_frames = AUDIO_CHUNK_S * AUDIO_SAMPLE_RATE
    with wave.open(str(wav), "rb") as reader:
        total_frames = max(1, reader.getnframes())
        while True:
            if cancel is not None and cancel.is_set():
                raise CommandCancelledError("audio analysis was cancelled")
            raw = reader.readframes(chunk_frames)
            if not raw:
                break
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / PCM16_SCALE
            # Pad the last chunk to whole seconds so every bin has full frames.
            padding = (-len(samples)) % AUDIO_SAMPLE_RATE
            if padding:
                samples = np.concatenate([samples, np.zeros(padding, dtype=np.float32)])
            rms, flux = chunk_features(samples)
            rms_all.extend(rms)
            flux_all.extend(flux)
            if on_progress is not None:
                on_progress(min(1.0, reader.tell() / total_frames))
    length = grid_length(duration_s)
    rms_arr = fit_length(np.asarray(rms_all), length)
    flux_arr = fit_length(np.asarray(flux_all), length)
    combined = (robust_z(rms_arr) + robust_z(flux_arr)) / 2.0
    return [float(value) for value in combined]


def run_audio(
    video_dir: Path,
    duration_s: float,
    *,
    ffmpeg_path: str | None,
    memory: MemoryInfo,
    cancel: threading.Event | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> SignalFile:
    """Write `audio.wav` and `signals/audio.json` (each skipped when a valid copy exists)."""
    wav = ensure_audio_wav(
        video_dir, duration_s, ffmpeg_path=ffmpeg_path, memory=memory, cancel=cancel
    )
    cached = load_signal(video_dir, "audio", duration_s)
    if cached is not None:
        return cached
    return save_signal(video_dir, "audio", audio_series(wav, duration_s, cancel, on_progress))
