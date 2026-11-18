"""Checkpoint list: per-step percent only for measured steps."""

from pathlib import Path

from app.db.models import VideoRow
from app.jobs.state import checkpoints


def video(stage: str, progress: float) -> VideoRow:
    return VideoRow(id="v", batch_id="b", position=0, url="u", status="running",
                    stage=stage, progress=progress, has_chat=0)  # fmt: skip


def running(stage: str, progress: float, tmp_path: Path) -> tuple[str, str | None]:
    step = next(
        c for c in checkpoints(video(stage, progress), [], tmp_path) if c.state == "running"
    )
    return step.key, step.detail


def test_running_step_shows_its_own_percent(tmp_path: Path) -> None:
    assert running("download", 0.175, tmp_path) == ("download", "50%")
    # transcribe covers 0.56-0.92 of the video's bar
    assert running("transcribe", 0.74, tmp_path) == ("transcribe", "50%")
    assert running("motion", 0.55, tmp_path) == ("signals", "100%")


def test_unmeasured_steps_show_no_number(tmp_path: Path) -> None:
    assert running("select", 0.95, tmp_path) == ("select", None)
    assert running("chat", 0.36, tmp_path) == ("chat", None)
