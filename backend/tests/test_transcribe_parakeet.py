# ruff: noqa: F401, F811
# (pytest fixtures imported from test_jobs_integration are used as test arguments.)
"""The Parakeet engine (D-028): engine choice, token → word joining, piece planning and
joining, segments, the transcript cache key, model download errors, the missing package,
and the pipeline fallback to Whisper. No real model is loaded here (the real runs are in
docs/PROGRESS.md)."""

import builtins
import io
import tarfile
import threading
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import PARAKEET_MODEL_NAME, PARAKEET_MODEL_URL, PARAKEET_WORD_MAX_S, AppConfig
from app.core.errors import AppError, ErrorCode
from app.core.hardware import MemoryInfo
from app.core.proc import CommandCancelledError
from app.core.settings_schema import DEFAULT_SETTINGS
from app.pipeline import transcribe_parakeet as tp
from app.pipeline.stt_engine import HINGLISH_NOTE, LOW_RAM_NOTE, choose_engine
from app.pipeline.transcribe import (
    TRANSCRIPT_FILE_NAME,
    Segment,
    Span,
    TranscriptFile,
    Word,
    load_cached,
    run_transcribe,
)
from tests.test_jobs_integration import (
    FFMPEG,
    KEY,
    FakeWhisper,
    build,
    finished,
    media,
    url,
    wait_for,
)

needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")

BIG = MemoryInfo(total_gb=15.7, available_gb=8.0)
SMALL = MemoryInfo(total_gb=3.9, available_gb=2.0)


# ----- engine choice -----


def test_default_engine_is_parakeet_and_whisper_stays_selectable() -> None:
    assert DEFAULT_SETTINGS.transcribe_engine == "parakeet"
    assert choose_engine("whisper", "en", BIG).engine == "whisper"
    assert choose_engine("whisper", "en", BIG).reason is None


def test_parakeet_for_english_whisper_for_hinglish_and_small_pcs() -> None:
    assert choose_engine("parakeet", "en", BIG).engine == "parakeet"
    hinglish = choose_engine("parakeet", "hinglish", BIG)
    assert (hinglish.engine, hinglish.reason) == ("whisper", HINGLISH_NOTE)
    low = choose_engine("parakeet", "en", SMALL)
    assert (low.engine, low.reason) == ("whisper", LOW_RAM_NOTE)


def test_settings_api_accepts_both_engines_only(client: TestClient) -> None:
    assert client.get("/api/settings").json()["transcribe_engine"] == "parakeet"
    assert client.put("/api/settings", json={"transcribe_engine": "whisper"}).status_code == 200
    assert client.get("/api/settings").json()["transcribe_engine"] == "whisper"
    bad = client.put("/api/settings", json={"transcribe_engine": "other"})
    assert bad.json()["error"]["code"] == "VALIDATION_ERROR"


# ----- tokens, pieces, segments -----


def test_tokens_join_into_words_with_offset() -> None:
    # Real token shapes from Parakeet: a leading space starts a word, a lone space token
    # marks the next token as a word start.
    tokens = [" T", "as", "k", "b", "ar", ".", " ", "Z", "oo", "'s", " not"]
    starts = [0.16, 0.24, 0.32, 0.48, 0.64, 1.12, 1.76, 1.92, 2.0, 2.16, 2.24]
    durations = [0.08, 0.08, 0.16, 0.16, 0.24, 0.24, 0.16, 0.0, 0.08, 0.08, 0.16]
    words = tp.tokens_to_words(tokens, starts, durations, offset_s=100.0)
    assert [w.text for w in words] == ["Taskbar.", "Zoo's", "not"]
    assert (words[0].start, words[0].end) == (100.16, 101.36)
    assert (words[1].start, words[1].end) == (101.92, 102.24)


def test_word_before_a_pause_is_not_stretched() -> None:
    words = tp.tokens_to_words([" Vatican"], [10.0], [4.72], offset_s=0.0)
    assert words[0].end == pytest.approx(10.0 + PARAKEET_WORD_MAX_S)


def test_pieces_end_only_at_region_ends_and_stay_short() -> None:
    regions = [(0, 10), (12, 25), (27, 40), (45, 47)]
    assert tp.plan_pieces(regions, 25) == [(0, 25), (27, 47)]
    assert tp.plan_pieces([(0, 100)], 25) == [(0, 100)]  # an over-long region stays whole
    assert tp.plan_pieces([], 25) == []


def _w(text: str, start: float, end: float) -> Word:
    return Word(text=text, start=start, end=end)


def test_word_heard_on_both_sides_of_a_cut_is_kept_once() -> None:
    # Measured case: "stay." ended a piece at 636.24 s and "Stay" began the next at 636.43 s.
    before = [_w("going", 635.76, 635.84), _w("stay.", 635.92, 636.24)]
    after = [_w("Stay", 636.43, 636.75), _w("dead", 636.75, 637.07)]
    joined = tp.join_pieces(before, after)
    assert [w.text for w in joined] == ["going", "Stay", "dead"]
    assert (joined[1].start, joined[1].end) == (635.92, 636.75)


def test_real_repeats_are_kept() -> None:
    far = tp.join_pieces([_w("no", 1.0, 1.2)], [_w("no", 2.0, 2.2)])
    assert [w.text for w in far] == ["no", "no"]
    other = tp.join_pieces([_w("go", 1.0, 1.2)], [_w("now", 1.3, 1.5)])
    assert [w.text for w in other] == ["go", "now"]


def test_segments_break_at_sentence_end_and_long_pause() -> None:
    words = [_w("Hi.", 0.0, 0.3), _w("Look", 0.5, 0.7), _w("here", 0.8, 1.0), _w("now", 2.5, 2.7)]
    segments = tp.words_to_segments(words)
    assert [s.text for s in segments] == ["Hi.", "Look here", "now"]
    assert (segments[1].start, segments[1].end) == (0.5, 1.0)
    assert tp.words_to_segments([]) == []


# ----- cache key -----


def _write_wav(path: Path, seconds: int) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(np.zeros(seconds * 16000, dtype="<i2").tobytes())


class OneWordRunner:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def transcribe(
        self, samples: Any, offset_s: float, language: str | None, initial_prompt: str | None,
        cancel: threading.Event | None, on_seconds: Callable[[float], None],
    ) -> tuple[list[Segment], str | None]:  # fmt: skip
        self.calls += 1
        word = _w(self.text, offset_s, offset_s + 0.5)
        return [Segment(start=word.start, end=word.end, text=self.text, words=[word])], "en"


def test_transcript_cache_is_per_engine(tmp_path: Path) -> None:
    wav = tmp_path / "audio.wav"
    _write_wav(wav, 3)
    spans = [Span(0.0, 3.0)]
    whisper = OneWordRunner("whisper")
    parakeet = OneWordRunner("parakeet")
    made = run_transcribe(
        tmp_path, wav, spans, caption_language="en", runner_factory=lambda: whisper
    )
    assert made.engine == "whisper"
    assert load_cached(tmp_path, "parakeet") is None
    again = run_transcribe(
        tmp_path, wav, spans, caption_language="en", runner_factory=lambda: parakeet,
        engine="parakeet",
    )  # fmt: skip
    assert parakeet.calls == 1
    assert again.engine == "parakeet"
    assert again.segments[0].words[0].text == "parakeet"
    cached = run_transcribe(
        tmp_path, wav, spans, caption_language="en", runner_factory=lambda: parakeet,
        engine="parakeet",
    )  # fmt: skip
    assert parakeet.calls == 1  # reused, not transcribed again
    assert cached.engine == "parakeet"


def test_transcript_from_before_d028_counts_as_whisper(tmp_path: Path) -> None:
    old = '{"version": 1, "language": "en", "spans": [[0.0, 1.0]], "segments": []}'
    (tmp_path / TRANSCRIPT_FILE_NAME).write_text(old, encoding="utf-8")
    loaded = load_cached(tmp_path, "whisper")
    assert loaded is not None
    assert loaded.made_by == "whisper"
    assert load_cached(tmp_path, "parakeet") is None
    assert load_cached(tmp_path) is not None  # callers that do not care still read it


# ----- model download -----


def _archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as tar:
        for name in tp.MODEL_FILES:
            data = b"x" * 10
            info = tarfile.TarInfo(f"{PARAKEET_MODEL_NAME}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, length: int | None = None) -> None:
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}


def test_model_downloads_once_with_progress(tmp_path: Path) -> None:
    data = _archive()
    seen: list[float] = []
    opened: list[str] = []

    def opener(link: str, timeout_s: float) -> FakeResponse:
        opened.append(link)
        return FakeResponse(data)

    folder = tp.ensure_model(tmp_path, on_progress=seen.append, opener=opener)
    assert all((folder / name).is_file() for name in tp.MODEL_FILES)
    assert seen[-1] == 1.0
    assert opened == [PARAKEET_MODEL_URL]
    assert sorted(p.name for p in folder.parent.iterdir()) == [PARAKEET_MODEL_NAME]
    tp.ensure_model(tmp_path, opener=opener)
    assert len(opened) == 1  # second call: already there


def test_incomplete_or_failed_download_gives_its_code(tmp_path: Path) -> None:
    data = _archive()

    def short(link: str, timeout_s: float) -> FakeResponse:
        return FakeResponse(data[:20], length=len(data))

    def offline(link: str, timeout_s: float) -> FakeResponse:
        raise OSError("no network")

    for opener in (short, offline):
        with pytest.raises(AppError) as caught:
            tp.ensure_model(tmp_path, opener=opener)
        assert caught.value.code == ErrorCode.MODEL_DOWNLOAD_FAILED
        assert not tp.model_ready(tmp_path)
        assert list((tmp_path / tp.PARAKEET_MODELS_DIR).iterdir()) == []  # no leftovers


def test_download_stops_on_cancel(tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(CommandCancelledError):
        tp.ensure_model(tmp_path, cancel=cancel, opener=lambda link, t: FakeResponse(_archive()))
    assert not tp.model_ready(tmp_path)


def test_missing_sherpa_onnx_gives_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_import = builtins.__import__

    def no_sherpa(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "sherpa_onnx":
            raise ImportError("No module named 'sherpa_onnx'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_sherpa)
    with pytest.raises(AppError) as caught:
        tp.ParakeetRunner(tmp_path, 1)
    assert caught.value.code == ErrorCode.TRANSCRIBE_FAILED
    assert "sherpa-onnx" in caught.value.message


# ----- the pipeline -----


class FakeParakeet(FakeWhisper):
    """Marks its words so the test can tell which engine wrote the transcript."""

    def transcribe(self, *args: Any, **kwargs: Any) -> tuple[list[Segment], str | None]:
        segments, language = super().transcribe(*args, **kwargs)
        for segment in segments:
            for word in segment.words:
                word.text = "pk" + word.text
        return segments, language


def _transcribe_step(video: dict[str, Any]) -> dict[str, Any]:
    return next(step for step in video["checkpoints"] if step["key"] == "transcribe")


def _run_one(app: Any, config: AppConfig) -> tuple[dict[str, Any], TranscriptFile]:
    with TestClient(app, raise_server_exceptions=False) as client:
        client.put("/api/llm/session", json={"provider": "gemini", "model": "m", "api_key": KEY})
        client.put("/api/settings", json={"style_defaults": {"clips_per_video": 1}})
        created = client.post("/api/batches", json={"urls": [url("aaaaaaaaaaa")]})
        batch = wait_for(client, created.json()["id"], finished)
    video = batch["videos"][0]
    transcript = load_cached(config.data_dir / "downloads" / video["id"])
    assert transcript is not None
    return video, transcript


@needs_ffmpeg
def test_pipeline_uses_parakeet_by_default(
    config: AppConfig,
    media: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build(config, media)
    ctx = app.state.job_context
    ctx.parakeet_factory = lambda cancel, on_download: FakeParakeet
    monkeypatch.setattr(ctx, "memory", lambda: BIG)
    video, transcript = _run_one(app, config)
    assert video["status"] == "done"
    # The engine shows on the Transcribe step only, not in the note (D-064).
    assert "Speech-to-text" not in (video["note"] or "")
    assert _transcribe_step(video)["engine"] == "Parakeet"
    assert transcript.engine == "parakeet"
    assert transcript.segments[0].words[0].text.startswith("pk")


class GpuThenCpuWhisper(FakeWhisper):
    """Starts on the GPU and, like WhisperRunner when CUDA fails at the first inference,
    ends on the CPU."""

    def __init__(self) -> None:
        self.device = "cuda"
        self.calls = 0

    def transcribe(self, *args: Any, **kwargs: Any) -> tuple[list[Segment], str | None]:
        self.calls += 1
        if self.calls == 1:
            self.device = "cpu"
        return super().transcribe(*args, **kwargs)


@needs_ffmpeg
def test_pipeline_uses_whisper_for_hinglish_and_shows_its_device(
    config: AppConfig,
    media: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.jobs import video_pipeline

    labels: list[str] = []
    original = video_pipeline.VideoPipeline._update

    def spy(self: Any, **fields: Any) -> Any:
        if "transcribe_engine" in fields:
            labels.append(fields["transcribe_engine"])
        return original(self, **fields)

    monkeypatch.setattr(video_pipeline.VideoPipeline, "_update", spy)
    app = build(config, media)
    ctx = app.state.job_context
    ctx.parakeet_factory = lambda cancel, on_download: FakeParakeet
    ctx.whisper_factory = lambda model, gpu: GpuThenCpuWhisper
    monkeypatch.setattr(ctx, "memory", lambda: BIG)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.put("/api/llm/session", json={"provider": "gemini", "model": "m", "api_key": KEY})
        client.put("/api/settings", json={"style_defaults": {"caption_language": "hinglish"}})
        created = client.post("/api/batches", json={"urls": [url("aaaaaaaaaaa")]})
        batch = wait_for(client, created.json()["id"], finished)
    video = batch["videos"][0]
    assert video["status"] == "done"
    assert "Hinglish uses Whisper" not in (video["note"] or "")  # log only (D-064)
    transcript = load_cached(config.data_dir / "downloads" / video["id"])
    assert transcript is not None
    assert transcript.engine == "whisper"
    # Named before loading, then the real device, then the CPU after the GPU gave up.
    assert labels == ["Whisper medium", "Whisper medium · GPU", "Whisper medium · CPU"]
    assert _transcribe_step(video)["engine"] == "Whisper medium · CPU"


@needs_ffmpeg
def test_pipeline_falls_back_to_whisper_when_parakeet_fails(
    config: AppConfig,
    media: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> Any:
        raise AppError(ErrorCode.MODEL_DOWNLOAD_FAILED, "no internet")

    app = build(config, media)
    ctx = app.state.job_context
    ctx.parakeet_factory = lambda cancel, on_download: broken
    monkeypatch.setattr(ctx, "memory", lambda: BIG)
    video, transcript = _run_one(app, config)
    assert video["status"] == "done"
    assert video["error"] is None
    # The fallback goes to the log; the note has no engine line (D-064).
    assert "Speech-to-text" not in (video["note"] or "")
    assert _transcribe_step(video)["engine"] == "Whisper medium"
    assert transcript.engine == "whisper"
    log = (config.logs_dir / "app.log").read_text(encoding="utf-8")
    assert "parakeet failed" in log
    assert "MODEL_DOWNLOAD_FAILED" in log
