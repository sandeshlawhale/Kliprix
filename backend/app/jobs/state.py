"""Derived state: batch status (docs/08 "State machines") and the per-video checkpoint list
(docs/04 "Checkpoints shown in the UI", D-024). Both come from rows plus stage files, so
they are right after a restart."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.db.models import ClipRow, VideoRow
from app.pipeline.candidates import CANDIDATES_FILE_NAME
from app.pipeline.chat import CHAT_FILE_NAME
from app.pipeline.download import INFO_FILE_NAME
from app.pipeline.select import SELECTION_FILE_NAME
from app.pipeline.signals import signal_path
from app.pipeline.transcribe import TRANSCRIPT_FILE_NAME

ACTIVE = ("queued", "running")

CHECKPOINTS: tuple[tuple[str, str], ...] = (
    ("download", "Download video"),
    ("chat", "Fetch chat replay"),
    ("signals", "Analyze audio and motion"),
    ("candidates", "Find highlights"),
    ("transcribe", "Transcribe"),
    ("select", "Pick clips"),
    ("edit", "Edit clips"),
)
STAGE_TO_CHECKPOINT = {
    "inspect": "download",
    "download": "download",
    "chat": "chat",
    "audio": "signals",
    "motion": "signals",
    "candidates": "candidates",
    "transcribe": "transcribe",
    "select": "select",
    "clips": "select",
}
_ORDER = [key for key, _ in CHECKPOINTS]


@dataclass(frozen=True)
class CheckpointState:
    key: str
    label: str
    state: str  # pending | running | done | skipped | failed
    detail: str | None = None


def derive_batch_status(current: str, videos: Sequence[VideoRow], clips: Sequence[ClipRow]) -> str:
    """queued/running while work remains; then done, partial or failed (docs/08)."""
    if current == "cancelled":
        return "cancelled"
    statuses = [v.status for v in videos] + [c.status for c in clips]
    if any(status in ACTIVE for status in statuses):
        started = any(status not in ("queued",) for status in statuses)
        return "running" if started else "queued"
    if videos and all(v.status in ("failed", "cancelled") for v in videos):
        return "failed"
    any_failed = any(v.status == "failed" for v in videos) or any(
        c.status == "failed" for c in clips
    )
    if any_failed:
        return "partial"
    if any(c.status == "done" for c in clips):
        return "done"
    return "failed"


def _files_done(video_dir: Path, key: str, clips: Sequence[ClipRow]) -> bool:
    if key == "download":
        return (video_dir / INFO_FILE_NAME).is_file()
    if key == "chat":
        return (video_dir / CHAT_FILE_NAME).is_file()
    if key == "signals":
        return (
            signal_path(video_dir, "audio").is_file() and signal_path(video_dir, "motion").is_file()
        )
    if key == "candidates":
        return (video_dir / CANDIDATES_FILE_NAME).is_file()
    if key == "transcribe":
        return (video_dir / TRANSCRIPT_FILE_NAME).is_file()
    if key == "select":
        return (video_dir / SELECTION_FILE_NAME).is_file() and bool(clips)
    return False


def checkpoints(
    video: VideoRow, clips: Sequence[ClipRow], video_dir: Path
) -> list[CheckpointState]:
    """The 7 checkpoints in fixed order for one video."""
    current = STAGE_TO_CHECKPOINT.get(video.stage or "")
    current_index = _ORDER.index(current) if current is not None else -1
    result: list[CheckpointState] = []
    for index, (key, label) in enumerate(CHECKPOINTS):
        if key == "edit":
            result.append(_edit_checkpoint(video, clips))
            continue
        if _files_done(video_dir, key, clips):
            result.append(CheckpointState(key, label, "done"))
            continue
        passed = video.status == "done" or (current_index > index)
        if key == "chat" and not video.has_chat and passed:
            result.append(CheckpointState(key, label, "skipped", "skipped, no chat replay"))
        elif video.status == "failed" and current == key:
            result.append(CheckpointState(key, label, "failed", video.error_message))
        elif video.status == "running" and current == key:
            result.append(CheckpointState(key, label, "running"))
        elif passed:
            result.append(CheckpointState(key, label, "done"))
        else:
            result.append(CheckpointState(key, label, "pending"))
    return result


def _edit_checkpoint(video: VideoRow, clips: Sequence[ClipRow]) -> CheckpointState:
    key, label = CHECKPOINTS[-1]
    if not clips:
        return CheckpointState(key, label, "failed" if video.status == "failed" else "pending")
    done = sum(1 for clip in clips if clip.status == "done")
    detail = f"{done}/{len(clips)}"
    if done == len(clips):
        return CheckpointState(key, label, "done", detail)
    if any(clip.status in ACTIVE for clip in clips):
        return CheckpointState(key, label, "running", detail)
    return CheckpointState(key, label, "failed", detail)
