"""Stage 7, transcribe: `transcript.json` with word timestamps (docs/04, 08, D-012).

Candidate-first: only candidate spans (candidate +/- 30 s, overlaps merged) are transcribed,
unless the video is at most 20 minutes long or "Thorough mode" is on. Each span is read from
`audio.wav` on its own, so a long WAV is never loaded whole. The Whisper model is loaded for
the stage and released afterwards (docs/11).
"""

from __future__ import annotations

import gc
import logging
import os
import threading
import wave
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import (
    AUDIO_SAMPLE_RATE,
    FULL_TRANSCRIBE_MAX_S,
    TRANSCRIBE_CHUNK_S,
    TRANSCRIBE_MARGIN_S,
    WHISPER_BEAM_SIZE,
)
from app.core.cuda_libs import register_cuda_libraries
from app.core.errors import AppError, ErrorCode
from app.core.files import write_json_atomic
from app.core.proc import CommandCancelledError
from app.pipeline.audio import PCM16_SCALE

logger = logging.getLogger(__name__)

TRANSCRIPT_FILE_NAME = "transcript.json"
WHISPER_MODELS_DIR = "whisper"
TIME_DECIMALS = 2

# Hinglish = Hindi in Roman letters (D-016, D-023). Whisper has no Hinglish mode; a Roman
# Hinglish prompt biases it toward Roman script (docs/04 "Hinglish handling").
HINGLISH_LANGUAGE = "hi"
HINGLISH_INITIAL_PROMPT = (
    "Namaste kaise ho aap? Main theek hoon. Kya baat hai, bhai! "
    "Yeh dekho, kitna mast hai. Chalo shuru karte hain."
)

# The Unicode Devanagari block.
DEVANAGARI_FIRST = chr(0x0900)
DEVANAGARI_LAST = chr(0x097F)

CaptionLanguage = Literal["en", "hinglish"]
# Which speech-to-text engine wrote a transcript (D-028). Files from before D-028 have none
# and were all made by Whisper.
Engine = Literal["whisper", "parakeet"]
# Called with the fraction (0.0-1.0) of the planned audio transcribed so far.
TranscribeProgress = Callable[[float], None]


class Word(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str
    words: list[Word]


class TranscriptFile(BaseModel):
    """`transcript.json`. `spans` lists the [start, end] ranges that were transcribed, so a
    later step can tell "no speech here" from "not transcribed" (AA-031)."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    language: str | None
    spans: list[tuple[float, float]]
    segments: list[Segment]
    engine: Engine | None = None

    @property
    def made_by(self) -> Engine:
        return self.engine or "whisper"


@dataclass(frozen=True)
class Span:
    start: float
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start


def candidate_spans(
    windows: Iterable[tuple[float, float]], duration_s: float, margin_s: float
) -> list[Span]:
    """Candidate windows widened by `margin_s` on both sides, clamped, and merged when they
    overlap or touch, sorted by start."""
    widened = sorted(
        (max(0.0, start - margin_s), min(duration_s, end + margin_s)) for start, end in windows
    )
    merged: list[Span] = []
    for start, end in widened:
        if merged and start <= merged[-1].end:
            last = merged[-1]
            merged[-1] = Span(last.start, max(last.end, end))
        else:
            merged.append(Span(start, end))
    return merged


def split_spans(spans: Sequence[Span], chunk_s: float) -> list[Span]:
    """Cut spans longer than `chunk_s` into pieces so one piece of audio stays small."""
    pieces: list[Span] = []
    for span in spans:
        start = span.start
        while span.end - start > chunk_s:
            pieces.append(Span(start, start + chunk_s))
            start += chunk_s
        pieces.append(Span(start, span.end))
    return pieces


def plan_spans(
    windows: Iterable[tuple[float, float]], duration_s: float, *, thorough: bool
) -> list[Span]:
    """Spans to transcribe: the whole video when it is at most 20 minutes or `thorough`,
    otherwise the candidate spans (docs/04 "candidate-first transcription")."""
    if thorough or duration_s <= FULL_TRANSCRIBE_MAX_S:
        spans = [Span(0.0, duration_s)]
    else:
        spans = candidate_spans(windows, duration_s, TRANSCRIBE_MARGIN_S)
    return split_spans(spans, TRANSCRIBE_CHUNK_S)


def read_span(wav: Path, span: Span) -> np.ndarray[Any, np.dtype[np.float32]]:
    """The samples of `span` from a 16 kHz mono 16-bit WAV as float32 in [-1, 1]."""
    with wave.open(str(wav), "rb") as reader:
        total = reader.getnframes()
        first = min(total, int(span.start * AUDIO_SAMPLE_RATE))
        count = max(0, min(total, int(span.end * AUDIO_SAMPLE_RATE)) - first)
        reader.setpos(first)
        raw = reader.readframes(count)
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / PCM16_SCALE


def contains_devanagari(text: str) -> bool:
    return any(DEVANAGARI_FIRST <= char <= DEVANAGARI_LAST for char in text)


class WhisperRunner:
    """Loads one faster-whisper model and transcribes arrays of 16 kHz audio.

    On a GPU the CUDA libraries are only loaded at the first inference, so a GPU failure
    there switches the runner to the CPU and repeats that piece (the CPU path always works).
    On the CPU it loads `cpu_model` when given: the GPU's turbo model is much slower on a CPU
    than `medium` (D-032). `model_name` is the model that runs now.
    """

    def __init__(
        self, model_name: str, models_dir: Path, use_gpu: bool, cpu_model: str | None = None
    ) -> None:
        # huggingface_hub (used by faster-whisper to fetch models) sends usage telemetry
        # unless this is set before it is imported (docs/11: no telemetry).
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        self._gpu_model = model_name
        self._cpu_model = cpu_model or model_name
        self.model_name = model_name
        self._download_root = str(models_dir / WHISPER_MODELS_DIR)
        self.device = "cpu"
        if use_gpu:
            # The GPU libraries from requirements-gpu.txt (D-031); none installed = CPU below.
            register_cuda_libraries()
            try:
                self.model = self._load("cuda")
                self.device = "cuda"
                return
            except (RuntimeError, ValueError, OSError) as exc:
                logger.warning("whisper on GPU unavailable, using CPU: %s", exc)
        self.model = self._load("cpu")

    def _load(self, device: str) -> Any:
        from faster_whisper import WhisperModel  # heavy: imported on demand

        compute_type = "int8_float16" if device == "cuda" else "int8"
        self.model_name = self._gpu_model if device == "cuda" else self._cpu_model
        return WhisperModel(
            self.model_name, device=device, compute_type=compute_type,
            download_root=self._download_root,
        )  # fmt: skip

    def transcribe(
        self,
        samples: np.ndarray[Any, np.dtype[np.float32]],
        offset_s: float,
        language: str | None,
        initial_prompt: str | None,
        cancel: threading.Event | None,
        on_seconds: Callable[[float], None],
    ) -> tuple[list[Segment], str | None]:
        """Segments with absolute times (offset added) and the detected language."""
        try:
            return self._transcribe(samples, offset_s, language, initial_prompt, cancel, on_seconds)
        except RuntimeError as exc:
            if self.device != "cuda":
                raise
            logger.warning("whisper failed on GPU, switching to CPU: %s", exc)
            self.model = None
            gc.collect()
            self.model = self._load("cpu")
            self.device = "cpu"
            return self._transcribe(samples, offset_s, language, initial_prompt, cancel, on_seconds)

    def _transcribe(
        self,
        samples: np.ndarray[Any, np.dtype[np.float32]],
        offset_s: float,
        language: str | None,
        initial_prompt: str | None,
        cancel: threading.Event | None,
        on_seconds: Callable[[float], None],
    ) -> tuple[list[Segment], str | None]:
        raw_segments, info = self.model.transcribe(
            samples,
            language=language,
            initial_prompt=initial_prompt,
            word_timestamps=True,
            vad_filter=True,
            beam_size=WHISPER_BEAM_SIZE,
        )
        segments: list[Segment] = []
        for raw in raw_segments:
            if cancel is not None and cancel.is_set():
                raise CommandCancelledError("transcription was cancelled")
            words = [
                Word(
                    text=word.word.strip(),
                    start=round(offset_s + word.start, TIME_DECIMALS),
                    end=round(offset_s + max(word.end, word.start), TIME_DECIMALS),
                )
                for word in raw.words or []
                if word.word.strip()
            ]
            segments.append(
                Segment(
                    start=round(offset_s + raw.start, TIME_DECIMALS),
                    end=round(offset_s + raw.end, TIME_DECIMALS),
                    text=raw.text.strip(),
                    words=words,
                )
            )
            on_seconds(raw.end)
        return segments, info.language


class SpeechRunner(Protocol):
    """What `run_transcribe` needs from a model runner (WhisperRunner, or a fake in tests)."""

    def transcribe(
        self,
        samples: np.ndarray[Any, np.dtype[np.float32]],
        offset_s: float,
        language: str | None,
        initial_prompt: str | None,
        cancel: threading.Event | None,
        on_seconds: Callable[[float], None],
    ) -> tuple[list[Segment], str | None]: ...


RunnerFactory = Callable[[], SpeechRunner]


def load_cached(video_dir: Path, engine: Engine | None = None) -> TranscriptFile | None:
    """The saved transcript, or None when there is none, it does not parse, or `engine` is
    given and another engine made it (a Whisper transcript is never reused as Parakeet's)."""
    path = video_dir / TRANSCRIPT_FILE_NAME
    if not path.is_file():
        return None
    try:
        cached = TranscriptFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None
    if engine is not None and cached.made_by != engine:
        return None
    return cached


def run_transcribe(
    video_dir: Path,
    wav: Path,
    spans: Sequence[Span],
    *,
    caption_language: CaptionLanguage,
    runner_factory: RunnerFactory,
    cancel: threading.Event | None = None,
    on_progress: TranscribeProgress | None = None,
    engine: Engine = "whisper",
) -> TranscriptFile:
    """Transcribe `spans` of `wav` into `transcript.json` (skipped when a valid copy made by
    the same `engine` exists).

    Raises:
        AppError TRANSCRIBE_FAILED: the model could not be loaded or failed.
        AppError MODEL_DOWNLOAD_FAILED: the runner could not download its model.
        CommandCancelledError: `cancel` was set.
    """
    cached = load_cached(video_dir, engine)
    if cached is not None:
        return cached
    hinglish = caption_language == "hinglish"
    language = HINGLISH_LANGUAGE if hinglish else None
    prompt = HINGLISH_INITIAL_PROMPT if hinglish else None
    total = sum(span.length for span in spans) or 1.0
    done = 0.0
    segments: list[Segment] = []
    languages: list[str] = []
    try:
        runner = runner_factory()
    except (RuntimeError, ValueError, OSError) as exc:
        logger.error("whisper model load failed: %s", exc)
        raise AppError(
            ErrorCode.TRANSCRIBE_FAILED,
            "The speech recognition model could not be loaded. Check the internet "
            "connection for the first download and the free disk space.",
        ) from exc
    try:
        for span in spans:
            if cancel is not None and cancel.is_set():
                raise CommandCancelledError("transcription was cancelled")
            samples = read_span(wav, span)
            base = done

            def report(seconds: float, base: float = base, span: Span = span) -> None:
                if on_progress is not None:
                    on_progress(min(1.0, (base + min(seconds, span.length)) / total))

            found, detected = runner.transcribe(
                samples, span.start, language, prompt, cancel, report
            )
            segments.extend(found)
            if detected and found:
                languages.append(detected)
            done += span.length
            if on_progress is not None:
                on_progress(min(1.0, done / total))
    except (RuntimeError, ValueError) as exc:
        logger.error("whisper failed: %s", exc)
        raise AppError(ErrorCode.TRANSCRIBE_FAILED, "Transcription failed.") from exc
    finally:
        # Release the model right away; on low-RAM machines it is the largest object.
        del runner
        gc.collect()
    language_code = max(set(languages), key=languages.count) if languages else None
    transcript = TranscriptFile(
        language=language_code,
        spans=[(round(s.start, TIME_DECIMALS), round(s.end, TIME_DECIMALS)) for s in spans],
        segments=segments,
        engine=engine,
    )
    write_json_atomic(video_dir / TRANSCRIPT_FILE_NAME, transcript)
    logger.info(
        "transcript saved engine=%s spans=%s segments=%s language=%s",
        engine,
        len(spans),
        len(segments),
        language_code,
    )
    return transcript
