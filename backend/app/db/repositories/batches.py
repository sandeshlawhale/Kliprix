"""Batches, videos, clips and jobs (docs/08). Every query of these tables lives here.

The caller owns the session and commits.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db.models import BatchRow, ClipRow, JobRow, VideoRow, utc_now
from app.sources.base import VideoMeta

# Job priorities: edit/preview work goes before the remaining background clips (docs/08).
PRIORITY_BACKGROUND = 0
PRIORITY_USER = 10
# Jobs whose ref is a video: the pipeline, and "more clips" from its download (D-034).
VIDEO_JOB_KINDS = ("video_pipeline", "video_more_clips")
# Order of job kinds inside one video: the pipeline first, then its clips.
KIND_ORDER = {"video_pipeline": 0, "video_more_clips": 0, "clip_render": 1, "clip_preview": 1}


def new_id() -> str:
    return str(uuid.uuid4())


# ---------- batches ----------


def create_batch(
    session: Session,
    urls: Sequence[str],
    style: dict[str, Any],
    title: str | None,
    known: Mapping[str, VideoMeta] | None = None,
) -> BatchRow:
    """A queued batch with one video row and one `video_pipeline` job per URL.

    `known` = metadata already read for some URLs (the home page's link check); it fills
    the title, channel and thumbnail until the pipeline inspects the link itself.
    """
    batch = BatchRow(id=new_id(), status="queued", title=title, style_json=json.dumps(style))
    session.add(batch)
    session.flush()
    for position, url in enumerate(urls):
        video = VideoRow(
            id=new_id(), batch_id=batch.id, position=position, url=url,
            status="queued", stage=None, progress=0.0, has_chat=0,
        )  # fmt: skip
        meta = (known or {}).get(url)
        if meta is not None:
            video.platform, video.source_id = meta.platform, meta.source_id
            video.title, video.channel = meta.title, meta.channel
            video.duration_s, video.thumbnail_path = meta.duration_s, meta.thumbnail_url
        session.add(video)
        session.flush()
        add_job(session, "video_pipeline", video.id, PRIORITY_BACKGROUND)
    return batch


def get_batch(session: Session, batch_id: str) -> BatchRow | None:
    return session.get(BatchRow, batch_id)


def list_batches(
    session: Session, limit: int, before: tuple[datetime, str] | None
) -> list[BatchRow]:
    """Latest activity first (D-055); `before` = (last_activity_at, id) of the last item
    already shown."""
    query = select(BatchRow).order_by(BatchRow.last_activity_at.desc(), BatchRow.id.desc())
    if before is not None:
        at, batch_id = before
        query = query.where(
            (BatchRow.last_activity_at < at)
            | ((BatchRow.last_activity_at == at) & (BatchRow.id < batch_id))
        )
    return list(session.scalars(query.limit(limit)))


def touch_batch(batch: BatchRow) -> None:
    """The user started new work in this batch (More clips, Resume): it moves to the top
    of the history (D-055)."""
    batch.last_activity_at = utc_now()


def working_batches(session: Session, batch_ids: Sequence[str]) -> set[str]:
    """The batches with a queued or running job for one of their videos or clips (paused
    jobs do not count): the sidebar marks them as working (D-055)."""
    if not batch_ids:
        return set()
    active = JobRow.status.in_(("queued", "running"))
    by_video = (
        select(VideoRow.batch_id)
        .join(JobRow, JobRow.ref_id == VideoRow.id)
        .where(active, VideoRow.batch_id.in_(batch_ids))
    )
    by_clip = (
        select(ClipRow.batch_id)
        .join(JobRow, JobRow.ref_id == ClipRow.id)
        .where(active, ClipRow.batch_id.in_(batch_ids))
    )
    return set(session.scalars(by_video.union(by_clip)))


def batch_counts(session: Session, batch_ids: Sequence[str]) -> dict[str, tuple[int, int]]:
    """(video_count, clip_count) per batch."""
    videos: dict[str, int] = {
        batch_id: count
        for batch_id, count in session.execute(
            select(VideoRow.batch_id, func.count())
            .where(VideoRow.batch_id.in_(batch_ids))
            .group_by(VideoRow.batch_id)
        )
    }
    clips: dict[str, int] = {
        batch_id: count
        for batch_id, count in session.execute(
            select(ClipRow.batch_id, func.count())
            .where(ClipRow.batch_id.in_(batch_ids))
            .group_by(ClipRow.batch_id)
        )
    }
    return {bid: (videos.get(bid, 0), clips.get(bid, 0)) for bid in batch_ids}


def delete_batch(session: Session, batch_id: str) -> None:
    video_ids = [v.id for v in videos_of(session, batch_id)]
    clip_ids = [c.id for c in clips_of_batch(session, batch_id)]
    session.execute(delete(JobRow).where(JobRow.ref_id.in_(video_ids + clip_ids)))
    session.execute(delete(BatchRow).where(BatchRow.id == batch_id))


# ---------- videos ----------


def videos_of(session: Session, batch_id: str) -> list[VideoRow]:
    query = select(VideoRow).where(VideoRow.batch_id == batch_id).order_by(VideoRow.position)
    return list(session.scalars(query))


def get_video(session: Session, video_id: str) -> VideoRow | None:
    return session.get(VideoRow, video_id)


# ---------- clips ----------


def clips_of_video(session: Session, video_id: str) -> list[ClipRow]:
    query = select(ClipRow).where(ClipRow.video_id == video_id).order_by(ClipRow.idx)
    return list(session.scalars(query))


def clips_of_batch(session: Session, batch_id: str) -> list[ClipRow]:
    query = select(ClipRow).where(ClipRow.batch_id == batch_id).order_by(ClipRow.idx)
    return list(session.scalars(query))


def get_clip(session: Session, clip_id: str) -> ClipRow | None:
    return session.get(ClipRow, clip_id)


def add_clip(session: Session, **fields: Any) -> ClipRow:
    clip = ClipRow(id=new_id(), **fields)
    session.add(clip)
    session.flush()
    return clip


# ---------- jobs ----------


def add_job(
    session: Session, kind: str, ref_id: str, priority: int, payload: dict[str, Any] | None = None
) -> JobRow:
    job = JobRow(
        id=new_id(), kind=kind, ref_id=ref_id, status="queued", attempts=0, priority=priority,
        payload_json=json.dumps(payload) if payload else None,
    )  # fmt: skip
    session.add(job)
    session.flush()
    return job


def get_job(session: Session, job_id: str) -> JobRow | None:
    return session.get(JobRow, job_id)


def active_job_for(session: Session, ref_id: str, kind: str) -> JobRow | None:
    query = select(JobRow).where(
        JobRow.ref_id == ref_id, JobRow.kind == kind, JobRow.status.in_(("queued", "running"))
    )
    return session.scalars(query).first()


def active_video_job(session: Session, video_id: str) -> JobRow | None:
    """A queued or running pipeline or "more clips" job of this video."""
    query = select(JobRow).where(
        JobRow.ref_id == video_id,
        JobRow.kind.in_(VIDEO_JOB_KINDS),
        JobRow.status.in_(("queued", "running")),
    )
    return session.scalars(query).first()


def last_video_job(session: Session, video_id: str) -> JobRow | None:
    """The newest pipeline or "more clips" job of this video, whatever its status."""
    query = (
        select(JobRow)
        .where(JobRow.ref_id == video_id, JobRow.kind.in_(VIDEO_JOB_KINDS))
        .order_by(JobRow.created_at.desc())
    )
    return session.scalars(query).first()


# A clip job queued this soon after its video job finished belongs to the same run.
_RUN_GAP_S = 10.0


def generation_started_at(
    session: Session, video_id: str, clip_ids: Sequence[str]
) -> datetime | None:
    """When the work now running for this video began, or None when nothing runs yet.

    Counts from the moment the runner picked the job up (`started_at`), so videos still
    waiting in the queue show no timer. The pipeline (or "more clips") job and the clip
    renders it queued count as one run, so the batch page's timer covers download to the
    last clip. A later re-render of one clip from the editor starts its own run.
    """
    active = ("queued", "running")
    video_jobs = list(
        session.scalars(
            select(JobRow)
            .where(JobRow.ref_id == video_id, JobRow.kind.in_(VIDEO_JOB_KINDS))
            .order_by(JobRow.created_at.desc())
        )
    )
    latest = video_jobs[0] if video_jobs else None
    if latest is not None and latest.status in active:
        return latest.started_at
    clip_jobs = (
        list(
            session.scalars(
                select(JobRow).where(JobRow.ref_id.in_(clip_ids), JobRow.status.in_(active))
            )
        )
        if clip_ids
        else []
    )
    if not clip_jobs:
        return None
    first = min(job.created_at for job in clip_jobs)
    if (
        latest is not None
        and latest.finished_at is not None
        and (first - latest.finished_at).total_seconds() <= _RUN_GAP_S
    ):
        return latest.started_at
    started = [job.started_at for job in clip_jobs if job.started_at is not None]
    return min(started) if started else None


def _sort_key(session: Session, job: JobRow) -> tuple[Any, ...]:
    """Queue order (docs/08): priority, batch creation, video position, clip index."""
    if job.kind in VIDEO_JOB_KINDS:
        video = session.get(VideoRow, job.ref_id)
        clip_idx = -1
    else:
        clip = session.get(ClipRow, job.ref_id)
        video = session.get(VideoRow, clip.video_id) if clip is not None else None
        clip_idx = clip.idx if clip is not None else 0
    batch = session.get(BatchRow, video.batch_id) if video is not None else None
    return (
        -job.priority,
        batch.created_at if batch is not None else job.created_at,
        video.position if video is not None else 0,
        KIND_ORDER.get(job.kind, 9),
        clip_idx,
        job.created_at,
    )


def next_job(session: Session) -> JobRow | None:
    queued = list(session.scalars(select(JobRow).where(JobRow.status == "queued")))
    if not queued:
        return None
    return min(queued, key=lambda job: _sort_key(session, job))


def queued_clip_positions(session: Session) -> dict[str, int]:
    """1-based position in the queue for each queued clip (for `queue_position`)."""
    queued = list(session.scalars(select(JobRow).where(JobRow.status == "queued")))
    ordered = sorted(queued, key=lambda job: _sort_key(session, job))
    return {
        job.ref_id: number
        for number, job in enumerate(ordered, start=1)
        if job.kind not in VIDEO_JOB_KINDS
    }


def requeue_running(session: Session) -> int:
    """On startup: jobs, videos and clips left `running` by a stopped process go back to
    `queued` (docs/08). Returns the number of jobs."""
    jobs = list(session.scalars(select(JobRow).where(JobRow.status == "running")))
    for job in jobs:
        job.status = "queued"
    session.execute(update(VideoRow).where(VideoRow.status == "running").values(status="queued"))
    session.execute(update(ClipRow).where(ClipRow.status == "running").values(status="queued"))
    return len(jobs)


def cancel_jobs(session: Session, ref_ids: Sequence[str]) -> list[JobRow]:
    """Mark queued, running and paused jobs for these refs cancelled; returns the running
    ones."""
    jobs = list(
        session.scalars(
            select(JobRow).where(
                JobRow.ref_id.in_(ref_ids), JobRow.status.in_(("queued", "running", "paused"))
            )
        )
    )
    running = [job for job in jobs if job.status == "running"]
    for job in jobs:
        job.status = "cancelled"
        job.finished_at = utc_now()
    return running


def pause_jobs(session: Session, ref_ids: Sequence[str]) -> int:
    """Pause (D-049): queued and running jobs for these refs become `paused`, so the runner
    skips them until resume. The running one is stopped by the runner and kept paused.
    Returns the number of jobs."""
    jobs = list(
        session.scalars(
            select(JobRow).where(
                JobRow.ref_id.in_(ref_ids), JobRow.status.in_(("queued", "running"))
            )
        )
    )
    for job in jobs:
        job.status = "paused"
    return len(jobs)


def resume_jobs(session: Session, ref_ids: Sequence[str]) -> int:
    """Resume (D-049): paused jobs for these refs are queued again (their old queue order)."""
    jobs = list(
        session.scalars(select(JobRow).where(JobRow.ref_id.in_(ref_ids), JobRow.status == "paused"))
    )
    for job in jobs:
        job.status = "queued"
    return len(jobs)
