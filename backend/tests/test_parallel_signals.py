"""Audio, motion and screen type run at the same time (D-033, D-040): results, progress,
failure and cancel; a screen type failure is not fatal."""

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.errors import AppError, ErrorCode
from app.core.hardware import MemoryInfo
from app.core.proc import CommandCancelledError
from app.jobs import video_pipeline
from app.jobs.video_pipeline import PROGRESS_AT, VideoPipeline
from app.pipeline.screen import ScreenFile
from app.pipeline.signals import SignalFile

MEMORY = MemoryInfo(total_gb=8.0, available_gb=4.0)
# Long enough that a stage waiting for its sibling never times out in a passing test.
WAIT_S = 10.0
notes: list[str] = []


def _pipeline(
    tmp_path: Path, cancel: threading.Event | None = None
) -> tuple[VideoPipeline, list[float]]:
    ctx = SimpleNamespace(
        video_dir=lambda _id: tmp_path,
        data_dir=tmp_path,
        config=SimpleNamespace(ffmpeg_path=None),
    )
    pipeline = VideoPipeline(ctx, "v1", cancel or threading.Event())  # type: ignore[arg-type]
    seen: list[float] = []
    notes.clear()

    def update(**fields: Any) -> Any:
        seen.append(fields["progress"])

    pipeline._update = update  # type: ignore[method-assign]
    pipeline._add_note = lambda text: notes.append(text)  # type: ignore[method-assign]
    return pipeline, seen


def _waits_for_stop(**kwargs: Any) -> SignalFile:
    """A stage that runs until it is stopped."""
    if kwargs["cancel"].wait(WAIT_S):
        raise CommandCancelledError("stopped")
    raise AssertionError("never stopped")


def _quick(value: float) -> Any:
    def run(*_args: Any, on_progress: Any, **_kwargs: Any) -> SignalFile:
        on_progress(0.5)
        on_progress(1.0)
        return SignalFile(values=[value] * 3)

    return run


def test_both_results_and_progress_across_both_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(video_pipeline, "run_audio", _quick(1.0))
    monkeypatch.setattr(video_pipeline, "run_motion", _quick(2.0))
    pipeline, seen = _pipeline(tmp_path)
    signals, screen = pipeline._audio_and_motion(3.0, 320, 180, MEMORY)
    assert screen is None
    assert signals["audio"].values == [1.0] * 3
    assert signals["motion"].values == [2.0] * 3
    assert seen == sorted(seen)
    assert seen[0] > PROGRESS_AT["chat"]
    assert seen[-1] == PROGRESS_AT["motion"]


def test_a_failure_stops_the_other_stage_and_is_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> SignalFile:
        raise AppError(ErrorCode.INTERNAL, "Could not read the audio track of the video.")

    monkeypatch.setattr(video_pipeline, "run_audio", broken)
    monkeypatch.setattr(video_pipeline, "run_motion", lambda *a, **k: _waits_for_stop(**k))
    pipeline, _ = _pipeline(tmp_path)
    with pytest.raises(AppError) as info:
        pipeline._audio_and_motion(3.0, 320, 180, MEMORY)
    assert info.value.code == ErrorCode.INTERNAL


def test_cancel_stops_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cancel = threading.Event()
    started = threading.Barrier(3)

    def stage(*_args: Any, **kwargs: Any) -> SignalFile:
        started.wait(WAIT_S)
        return _waits_for_stop(**kwargs)

    monkeypatch.setattr(video_pipeline, "run_audio", stage)
    monkeypatch.setattr(video_pipeline, "run_motion", stage)
    pipeline, _ = _pipeline(tmp_path, cancel)

    def cancel_once_both_run() -> None:
        started.wait(WAIT_S)
        cancel.set()

    threading.Thread(target=cancel_once_both_run, daemon=True).start()
    with pytest.raises(CommandCancelledError):
        pipeline._audio_and_motion(3.0, 320, 180, MEMORY)


def test_screen_failure_is_noted_not_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise AppError(ErrorCode.MODEL_DOWNLOAD_FAILED, "no connection")

    monkeypatch.setattr(video_pipeline, "run_audio", _quick(1.0))
    monkeypatch.setattr(video_pipeline, "run_motion", _quick(2.0))
    monkeypatch.setattr(video_pipeline, "run_screen", broken)
    pipeline, seen = _pipeline(tmp_path)
    signals, screen = pipeline._audio_and_motion(3.0, 320, 180, MEMORY)
    assert screen is None
    assert set(signals) == {"audio", "motion"}
    assert notes == [video_pipeline.SCREEN_FAILED_NOTE]
    assert seen[-1] == PROGRESS_AT["motion"]


def test_screen_result_is_returned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = ScreenFile(revision="r", step_s=5, groups=["gameplay"], shares=[[1.0]])

    def screen(*_args: Any, on_progress: Any, **_kwargs: Any) -> ScreenFile:
        on_progress(1.0)
        return result

    monkeypatch.setattr(video_pipeline, "run_audio", _quick(1.0))
    monkeypatch.setattr(video_pipeline, "run_motion", _quick(2.0))
    monkeypatch.setattr(video_pipeline, "run_screen", screen)
    pipeline, _ = _pipeline(tmp_path)
    _, got = pipeline._audio_and_motion(3.0, 320, 180, MEMORY)
    assert got == result
