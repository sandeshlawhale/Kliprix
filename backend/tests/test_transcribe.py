"""Transcription planning, span reading, the stage with a fake model, and Hinglish words."""

import threading
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.core.errors import AppError, ErrorCode
from app.pipeline.hinglish import needs_transliteration, to_hinglish
from app.pipeline.transcribe import (
    HINGLISH_INITIAL_PROMPT,
    TRANSCRIPT_FILE_NAME,
    Segment,
    Span,
    Word,
    candidate_spans,
    contains_devanagari,
    load_cached,
    plan_spans,
    read_span,
    run_transcribe,
    split_spans,
)

RATE = 16000


def test_candidate_spans_widen_clamp_and_merge() -> None:
    windows = [(100.0, 160.0), (200.0, 260.0), (500.0, 560.0), (10.0, 70.0)]
    spans = candidate_spans(windows, 580.0, 30.0)
    assert spans == [Span(0.0, 290.0), Span(470.0, 580.0)]


def test_split_spans_bounds_piece_length() -> None:
    pieces = split_spans([Span(0.0, 1300.0)], 600.0)
    assert pieces == [Span(0.0, 600.0), Span(600.0, 1200.0), Span(1200.0, 1300.0)]


def test_plan_spans_full_for_short_or_thorough() -> None:
    windows = [(100.0, 160.0)]
    assert plan_spans(windows, 900.0, thorough=False) == [Span(0.0, 600.0), Span(600.0, 900.0)]
    assert plan_spans(windows, 3600.0, thorough=False) == [Span(70.0, 190.0)]
    assert len(plan_spans(windows, 3600.0, thorough=True)) == 6


def _write_wav(path: Path, seconds: int) -> None:
    # Sample value = the second it belongs to, so reads can be checked exactly.
    samples = np.repeat(np.arange(seconds, dtype="<i2"), RATE)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(samples.tobytes())


def test_read_span_reads_only_that_range(tmp_path: Path) -> None:
    wav = tmp_path / "audio.wav"
    _write_wav(wav, 10)
    samples = read_span(wav, Span(3.0, 5.0))
    assert len(samples) == 2 * RATE
    assert samples[0] == pytest.approx(3 / 32768)
    assert samples[-1] == pytest.approx(4 / 32768)
    assert len(read_span(wav, Span(9.0, 20.0))) == RATE


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[float, int, str | None, str | None]] = []

    def transcribe(
        self,
        samples: np.ndarray[Any, np.dtype[np.float32]],
        offset_s: float,
        language: str | None,
        initial_prompt: str | None,
        cancel: threading.Event | None,
        on_seconds: Callable[[float], None],
    ) -> tuple[list[Segment], str | None]:
        self.calls.append((offset_s, len(samples), language, initial_prompt))
        word = Word(text="hi", start=offset_s + 1.0, end=offset_s + 1.4)
        on_seconds(2.0)
        segment = Segment(start=offset_s + 1.0, end=offset_s + 1.4, text="hi", words=[word])
        return [segment], "en"


def test_run_transcribe_offsets_spans_and_caches(tmp_path: Path) -> None:
    wav = tmp_path / "audio.wav"
    _write_wav(wav, 30)
    runner = FakeRunner()
    progress: list[float] = []
    spans = [Span(0.0, 10.0), Span(20.0, 30.0)]
    result = run_transcribe(
        tmp_path, wav, spans, caption_language="en",
        runner_factory=lambda: runner, on_progress=progress.append,
    )  # fmt: skip
    assert [c[0] for c in runner.calls] == [0.0, 20.0]
    assert runner.calls[0][2:] == (None, None)
    assert [s.start for s in result.segments] == [1.0, 21.0]
    assert result.language == "en"
    assert result.spans == [(0.0, 10.0), (20.0, 30.0)]
    assert progress[-1] == 1.0
    assert progress == sorted(progress)
    assert load_cached(tmp_path) == result
    # Cached: the model is not loaded again.
    again = run_transcribe(
        tmp_path, wav, spans, caption_language="en", runner_factory=_fail_factory
    )
    assert again == result


def _fail_factory() -> FakeRunner:
    raise RuntimeError("model download failed")


def test_run_transcribe_hinglish_uses_roman_prompt(tmp_path: Path) -> None:
    wav = tmp_path / "audio.wav"
    _write_wav(wav, 5)
    runner = FakeRunner()
    run_transcribe(
        tmp_path, wav, [Span(0.0, 5.0)], caption_language="hinglish",
        runner_factory=lambda: runner,
    )  # fmt: skip
    assert runner.calls[0][2:] == ("hi", HINGLISH_INITIAL_PROMPT)


def test_run_transcribe_model_failure_maps_code(tmp_path: Path) -> None:
    wav = tmp_path / "audio.wav"
    _write_wav(wav, 5)
    with pytest.raises(AppError) as info:
        run_transcribe(
            tmp_path, wav, [Span(0.0, 5.0)], caption_language="en", runner_factory=_fail_factory
        )
    assert info.value.code == ErrorCode.TRANSCRIBE_FAILED
    assert not (tmp_path / TRANSCRIPT_FILE_NAME).exists()


def test_silent_video_gives_empty_transcript(tmp_path: Path) -> None:
    class Silent(FakeRunner):
        def transcribe(self, *args: Any, **kwargs: Any) -> tuple[list[Segment], str | None]:
            return [], "en"

    wav = tmp_path / "audio.wav"
    _write_wav(wav, 5)
    result = run_transcribe(
        tmp_path, wav, [Span(0.0, 5.0)], caption_language="en", runner_factory=Silent
    )
    assert result.segments == []
    assert result.language is None


DEVANAGARI_WORDS = ["\u0915\u094d\u092f\u093e", "\u092c\u093e\u0924", "OK", "\u0939\u0948"]


def _words(texts: list[str]) -> list[Word]:
    return [Word(text=t, start=i * 0.5, end=i * 0.5 + 0.4) for i, t in enumerate(texts)]


def test_contains_devanagari() -> None:
    assert contains_devanagari(DEVANAGARI_WORDS[0])
    assert not contains_devanagari("kya baat hai")


def test_to_hinglish_keeps_count_and_timing() -> None:
    words = _words(DEVANAGARI_WORDS)
    result = to_hinglish(words, lambda texts: ["kya", "baat", "OK", "hai"])
    assert [w.text for w in result.words] == ["kya", "baat", "OK", "hai"]
    assert [(w.start, w.end) for w in result.words] == [(w.start, w.end) for w in words]
    assert not result.devanagari_left


def test_to_hinglish_rejects_wrong_length_and_failure() -> None:
    words = _words(DEVANAGARI_WORDS)
    short = to_hinglish(words, lambda texts: ["kya", "baat hai"])
    assert short.words == words
    assert short.devanagari_left
    failed = to_hinglish(words, lambda texts: None)
    assert failed.devanagari_left


def test_to_hinglish_skips_llm_without_devanagari() -> None:
    words = _words(["kya", "baat"])

    def never(texts: list[str]) -> list[str] | None:
        raise AssertionError("not called")

    assert to_hinglish(words, never).words == words
    assert not needs_transliteration(words)
