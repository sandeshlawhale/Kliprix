"""The Parakeet speech-to-text engine (D-028): NVIDIA Parakeet-TDT-0.6b-v2 int8 through
sherpa-onnx, English only.

`ParakeetRunner` has the same interface as `WhisperRunner` (`SpeechRunner`), so
`run_transcribe` (span planning, chunked reading of `audio.wav`, progress, caching) is shared
and `transcript.json` keeps its schema. Inside one span, voice detection (Silero, the same
model Whisper's `vad_filter` uses) finds the speech; silent parts are skipped and pieces of at
most 30 s are cut only in pauses, so a word is not split between two pieces.

The model is downloaded at first use into `data/models/parakeet`. sherpa-onnx is imported
only when a runner is created, so the app starts without it.
"""

from __future__ import annotations

import logging
import re
import shutil
import tarfile
import threading
import urllib.error
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from app.config import (
    AUDIO_SAMPLE_RATE,
    PARAKEET_DUPLICATE_GAP_S,
    PARAKEET_MODEL_NAME,
    PARAKEET_MODEL_URL,
    PARAKEET_PIECE_S,
    PARAKEET_SEGMENT_GAP_S,
    PARAKEET_VAD_MIN_SILENCE_MS,
    PARAKEET_VAD_PAD_MS,
    PARAKEET_WORD_MAX_S,
)
from app.core.errors import AppError, ErrorCode
from app.core.hardware import disk_free_gb
from app.core.model_download import DownloadProgress, download_stream, open_url
from app.core.proc import CommandCancelledError
from app.pipeline.transcribe import TIME_DECIMALS, Segment, Word

logger = logging.getLogger(__name__)

PARAKEET_MODELS_DIR = "parakeet"
PARAKEET_LANGUAGE = "en"
MODEL_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
# The archive is 0.48 GB and unpacks to 0.66 GB; both exist for a moment during setup.
MODEL_DISK_NEEDED_GB = 1.5
SENTENCE_END = (".", "?", "!")
_NOT_WORD = re.compile(r"[^\w']+")

Samples = np.ndarray[Any, np.dtype[np.float32]]


# ----- model files -----


def model_dir(models_dir: Path) -> Path:
    return models_dir / PARAKEET_MODELS_DIR / PARAKEET_MODEL_NAME


def model_ready(models_dir: Path) -> bool:
    folder = model_dir(models_dir)
    return all((folder / name).is_file() for name in MODEL_FILES)


def ensure_model(
    models_dir: Path,
    *,
    cancel: threading.Event | None = None,
    on_progress: DownloadProgress | None = None,
    opener: Callable[[str, float], Any] | None = None,
) -> Path:
    """The model folder, downloading and unpacking the archive first when it is missing.

    Raises:
        AppError MODEL_DOWNLOAD_FAILED: no connection, an incomplete download, or a bad archive.
        AppError DISK_FULL: less than 1.5 GB free for the download.
        CommandCancelledError: `cancel` was set during the download.
    """
    folder = model_dir(models_dir)
    if model_ready(models_dir):
        return folder
    root = folder.parent
    root.mkdir(parents=True, exist_ok=True)
    free = disk_free_gb(root)
    if free < MODEL_DISK_NEEDED_GB:
        raise AppError(
            ErrorCode.DISK_FULL,
            "Not enough free disk space to download the Parakeet speech model.",
            {"needed_gb": MODEL_DISK_NEEDED_GB, "free_gb": round(free, 1)},
        )
    archive = root / f"{PARAKEET_MODEL_NAME}.tar.bz2"
    partial = archive.with_name(archive.name + ".part")
    logger.info("parakeet model download started url=%s", PARAKEET_MODEL_URL)
    try:
        download_stream(PARAKEET_MODEL_URL, partial, cancel, on_progress, opener or open_url)
        partial.replace(archive)
        _unpack(archive, root)
    except (urllib.error.URLError, OSError, tarfile.TarError) as exc:
        logger.error("parakeet model download failed: %s", exc)
        raise AppError(
            ErrorCode.MODEL_DOWNLOAD_FAILED,
            "The Parakeet speech model could not be downloaded. Check the internet "
            "connection; the next video tries again.",
        ) from exc
    finally:
        partial.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
    if not model_ready(models_dir):
        raise AppError(
            ErrorCode.MODEL_DOWNLOAD_FAILED,
            "The downloaded Parakeet speech model is missing files.",
        )
    logger.info("parakeet model ready path=%s", folder)
    return folder


def _unpack(archive: Path, root: Path) -> None:
    """Unpack into a temporary folder, then move the model folder into place, so a cut-off
    unpack never looks like a finished model."""
    staging = root / f"{PARAKEET_MODEL_NAME}.unpacking"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        with tarfile.open(archive, "r:bz2") as tar:
            # "data" refuses absolute paths, links outside the folder, and device files.
            tar.extractall(staging, filter="data")
        unpacked = staging / PARAKEET_MODEL_NAME
        if not unpacked.is_dir():
            raise tarfile.TarError(f"archive has no {PARAKEET_MODEL_NAME} folder")
        target = root / PARAKEET_MODEL_NAME
        shutil.rmtree(target, ignore_errors=True)
        unpacked.replace(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ----- pure helpers (unit-tested) -----


def plan_pieces(regions: Sequence[tuple[int, int]], max_samples: int) -> list[tuple[int, int]]:
    """Speech regions (sample ranges, sorted) joined into pieces of at most `max_samples`.
    A piece only ever ends where a region ends, i.e. in a pause; a region longer than the
    limit is a piece of its own (voice detection already keeps regions under it)."""
    pieces: list[tuple[int, int]] = []
    for start, end in regions:
        if pieces and end - pieces[-1][0] <= max_samples:
            pieces[-1] = (pieces[-1][0], end)
        else:
            pieces.append((start, end))
    return pieces


def tokens_to_words(
    tokens: Sequence[str],
    starts: Sequence[float],
    durations: Sequence[float],
    offset_s: float,
) -> list[Word]:
    """Parakeet's sub-word tokens joined into words with absolute times.

    A token that begins with a space starts a new word (a lone space token marks the next
    token as a word start). A word ends where its last token ends, but at most
    `PARAKEET_WORD_MAX_S` after it starts, so a word before a pause is not stretched over it.
    """
    words: list[Word] = []
    text = ""
    start = end = 0.0
    new_word = True

    def flush() -> None:
        if text:
            first = offset_s + start
            last = offset_s + min(end, start + PARAKEET_WORD_MAX_S)
            words.append(
                Word(
                    text=text,
                    start=round(first, TIME_DECIMALS),
                    end=round(max(first, last), TIME_DECIMALS),
                )
            )

    for index, token in enumerate(tokens):
        token_start = starts[index]
        token_end = token_start + (durations[index] if index < len(durations) else 0.0)
        if not token.strip():
            new_word = True
            continue
        if new_word or token[0].isspace() or not text:
            flush()
            text, start = token.strip(), token_start
        else:
            text += token
        end = token_end
        new_word = False
    flush()
    return words


def _plain(text: str) -> str:
    return _NOT_WORD.sub("", text.lower())


def join_pieces(previous: list[Word], following: list[Word]) -> list[Word]:
    """`previous + following`, but a word said once and heard on both sides of a piece
    boundary (same word, almost no gap) is kept once, with the first copy's start."""
    if previous and following:
        last, first = previous[-1], following[0]
        if (
            _plain(last.text)
            and _plain(last.text) == _plain(first.text)
            and first.start - last.end <= PARAKEET_DUPLICATE_GAP_S
        ):
            merged = Word(text=first.text, start=last.start, end=max(last.end, first.end))
            return [*previous[:-1], merged, *following[1:]]
    return [*previous, *following]


def words_to_segments(words: Sequence[Word]) -> list[Segment]:
    """Caption segments: a new one after sentence punctuation or a pause of 1 s or more."""
    segments: list[Segment] = []
    current: list[Word] = []
    for word in words:
        if current and (
            current[-1].text.endswith(SENTENCE_END)
            or word.start - current[-1].end >= PARAKEET_SEGMENT_GAP_S
        ):
            segments.append(_segment(current))
            current = []
        current.append(word)
    if current:
        segments.append(_segment(current))
    return segments


def _segment(words: list[Word]) -> Segment:
    return Segment(
        start=words[0].start,
        end=words[-1].end,
        text=" ".join(word.text for word in words),
        words=list(words),
    )


# ----- the runner -----


def speech_regions(samples: Samples) -> list[tuple[int, int]]:
    """Sample ranges with speech (Silero VAD from faster-whisper), each at most one piece."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps  # heavy: on demand

    options = VadOptions(
        min_silence_duration_ms=PARAKEET_VAD_MIN_SILENCE_MS,
        speech_pad_ms=PARAKEET_VAD_PAD_MS,
        max_speech_duration_s=PARAKEET_PIECE_S,
    )
    found = get_speech_timestamps(samples, options, sampling_rate=AUDIO_SAMPLE_RATE)
    return [(int(item["start"]), int(item["end"])) for item in found]


class ParakeetRunner:
    """Loads Parakeet once and transcribes arrays of 16 kHz audio (a `SpeechRunner`).

    Raises AppError TRANSCRIBE_FAILED when sherpa-onnx is not installed or the model does not
    load, and AppError MODEL_DOWNLOAD_FAILED / DISK_FULL when the first download fails.
    """

    device = "cpu"

    def __init__(
        self,
        models_dir: Path,
        threads: int,
        *,
        cancel: threading.Event | None = None,
        on_download: DownloadProgress | None = None,
    ) -> None:
        try:
            import sherpa_onnx  # heavy and optional: imported only when Parakeet is used
        except ImportError as exc:
            raise AppError(
                ErrorCode.TRANSCRIBE_FAILED,
                "Parakeet needs the sherpa-onnx package, which is not installed.",
            ) from exc
        folder = ensure_model(models_dir, cancel=cancel, on_progress=on_download)
        try:
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(folder / MODEL_FILES[0]),
                decoder=str(folder / MODEL_FILES[1]),
                joiner=str(folder / MODEL_FILES[2]),
                tokens=str(folder / MODEL_FILES[3]),
                num_threads=threads,
                sample_rate=AUDIO_SAMPLE_RATE,
                decoding_method="greedy_search",
                model_type="nemo_transducer",
            )
        except (RuntimeError, ValueError, OSError) as exc:
            raise AppError(
                ErrorCode.TRANSCRIBE_FAILED, "The Parakeet speech model could not be loaded."
            ) from exc
        logger.info("parakeet loaded threads=%s", threads)

    def transcribe(
        self,
        samples: Samples,
        offset_s: float,
        language: str | None,
        initial_prompt: str | None,
        cancel: threading.Event | None,
        on_seconds: Callable[[float], None],
    ) -> tuple[list[Segment], str | None]:
        """Segments with absolute times (offset added). `language` and `initial_prompt` are
        Whisper options; Parakeet only knows English, so the caller never sends Hinglish."""
        max_samples = int(PARAKEET_PIECE_S * AUDIO_SAMPLE_RATE)
        words: list[Word] = []
        for start, end in plan_pieces(speech_regions(samples), max_samples):
            if cancel is not None and cancel.is_set():
                raise CommandCancelledError("transcription was cancelled")
            stream = self._recognizer.create_stream()
            stream.accept_waveform(AUDIO_SAMPLE_RATE, samples[start:end])
            self._recognizer.decode_stream(stream)
            result = stream.result
            found = tokens_to_words(
                list(result.tokens),
                list(result.timestamps),
                list(result.durations),
                offset_s + start / AUDIO_SAMPLE_RATE,
            )
            words = join_pieces(words, found)
            on_seconds(end / AUDIO_SAMPLE_RATE)
        segments = words_to_segments(words)
        return segments, PARAKEET_LANGUAGE if segments else None
