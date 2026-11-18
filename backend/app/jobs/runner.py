"""The job runner: one background thread that runs one job at a time (docs/06, 08).

On start, jobs left `running` by a stopped process go back to `queued` and run again; their
stages skip cached outputs. A failing job marks only its own video or clip failed.
Cancel sets the running job's event (subprocesses are killed) and removes partial files.
"""

from __future__ import annotations

import logging
import shutil
import threading
from collections.abc import Sequence

from app.core.errors import AppError, ErrorCode
from app.core.proc import CommandCancelledError, CommandError
from app.db.models import JobRow, utc_now
from app.db.repositories import batches as repo
from app.jobs.clip_jobs import ClipJob
from app.jobs.context import CancelToken, JobContext
from app.jobs.state import derive_batch_status
from app.jobs.video_pipeline import VideoPipeline
from app.sources.base import SourceCancelledError

logger = logging.getLogger(__name__)

# How long the idle runner sleeps before looking for work again (it is also woken up).
IDLE_WAIT_S = 2.0
MESSAGE_LIMIT = 500


class JobRunner:
    def __init__(self, ctx: JobContext) -> None:
        self.ctx = ctx
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._current: CancelToken | None = None
        self._thread = threading.Thread(target=self._loop, name="job-runner", daemon=True)

    # ----- lifecycle -----

    def start(self) -> None:
        with self.ctx.session_factory() as session:
            count = repo.requeue_running(session)
            session.commit()
        if count:
            logger.info("resume: %s interrupted job(s) queued again", count)
        self._thread.start()

    def stop(self, timeout_s: float = 10.0) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            if self._current is not None:
                self._current.event.set()
        self._thread.join(timeout_s)

    def wake(self) -> None:
        self._wake.set()

    def cancel_refs(self, ref_ids: Sequence[str]) -> None:
        """Stop the running job if it belongs to one of these videos/clips."""
        with self._lock:
            if self._current is not None and self._current.ref_id in ref_ids:
                self._current.event.set()

    # ----- loop -----

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self._claim()
            if job is None:
                self._wake.wait(IDLE_WAIT_S)
                self._wake.clear()
                continue
            self._run(job)

    def _claim(self) -> JobRow | None:
        with self.ctx.session_factory() as session:
            job = repo.next_job(session)
            if job is None:
                return None
            job.status = "running"
            job.attempts += 1
            job.started_at = utc_now()
            session.commit()
            session.expunge(job)
            return job

    def _batch_of(self, job: JobRow) -> str | None:
        with self.ctx.session_factory() as session:
            if job.kind == "video_pipeline":
                video = repo.get_video(session, job.ref_id)
                return video.batch_id if video else None
            clip = repo.get_clip(session, job.ref_id)
            return clip.batch_id if clip else None

    def _run(self, job: JobRow) -> None:
        batch_id = self._batch_of(job)
        if batch_id is None:
            self._finish(job, "cancelled", None)
            return
        token = CancelToken(job.id, job.ref_id, batch_id)
        with self._lock:
            self._current = token
        self._mark_batch_started(batch_id)
        try:
            if job.kind == "video_pipeline":
                VideoPipeline(self.ctx, job.ref_id, token.event).run()
            else:
                ClipJob(self.ctx, job.ref_id, job.kind == "clip_preview", token.event).run()
            self._finish(job, "done", None)
        except (CommandCancelledError, SourceCancelledError):
            if self._stop.is_set():
                # Shutdown, not a user cancel: leave it for the next start to resume.
                logger.info("job interrupted by shutdown id=%s kind=%s", job.id, job.kind)
                self._mark_ref(job, "queued", None)
                self._finish(job, "queued", None)
                return
            logger.info("job cancelled id=%s kind=%s", job.id, job.kind)
            self._mark_ref(job, "cancelled", None)
            self._finish(job, "cancelled", None)
            self._cleanup_partial(job)
        except AppError as exc:
            logger.warning("job failed id=%s kind=%s code=%s", job.id, job.kind, exc.code.value)
            self._mark_ref(job, "failed", exc)
            self._finish(job, "failed", exc)
        except CommandError as exc:
            logger.error("job failed id=%s kind=%s: %s", job.id, job.kind, exc)
            code = (
                ErrorCode.TRANSCRIBE_FAILED
                if job.kind == "video_pipeline"
                else ErrorCode.RENDER_FAILED
            )
            error = AppError(code, "A processing step failed or took too long. Retry it.")
            self._mark_ref(job, "failed", error)
            self._finish(job, "failed", error)
        except Exception:  # last line of defence: one job never stops the runner
            logger.exception("job crashed id=%s kind=%s", job.id, job.kind)
            error = AppError(ErrorCode.INTERNAL, "Something went wrong. See the log file.")
            self._mark_ref(job, "failed", error)
            self._finish(job, "failed", error)
        finally:
            with self._lock:
                self._current = None
            self._refresh_batch(batch_id)

    def _finish(self, job: JobRow, status: str, error: AppError | None) -> None:
        with self.ctx.session_factory() as session:
            row = repo.get_job(session, job.id)
            if row is None:
                return
            # A cancel from the API already marked it; keep that.
            row.status = "cancelled" if row.status == "cancelled" else status
            row.finished_at = utc_now() if row.status != "queued" else None
            if error is not None:
                row.error_code = error.code.value
                row.error_message = error.message[:MESSAGE_LIMIT]
            session.commit()

    def _mark_ref(self, job: JobRow, status: str, error: AppError | None) -> None:
        with self.ctx.session_factory() as session:
            row = (
                repo.get_video(session, job.ref_id)
                if job.kind == "video_pipeline"
                else repo.get_clip(session, job.ref_id)
            )
            if row is None:
                return
            row.status = status
            row.error_code = error.code.value if error else None
            row.error_message = error.message[:MESSAGE_LIMIT] if error else None
            session.commit()
            kind = "video" if job.kind == "video_pipeline" else "clip"
            self.ctx.publish(
                row.batch_id, kind, id=row.id, status=row.status, stage=row.stage,
                progress=row.progress,
            )  # fmt: skip

    def _cleanup_partial(self, job: JobRow) -> None:
        """Cancelled clip work is partial: remove its work folder (docs/04 "Cancellation")."""
        if job.kind != "video_pipeline":
            shutil.rmtree(self.ctx.clip_work_dir(job.ref_id), ignore_errors=True)

    def _mark_batch_started(self, batch_id: str) -> None:
        """A job of the batch is starting, so the batch is running (not just queued)."""
        with self.ctx.session_factory() as session:
            batch = repo.get_batch(session, batch_id)
            if batch is not None and batch.status in ("queued", "done", "partial", "failed"):
                batch.status = "running"
                session.commit()
                self.ctx.publish(batch_id, "batch", status="running")

    def _refresh_batch(self, batch_id: str) -> None:
        with self.ctx.session_factory() as session:
            batch = repo.get_batch(session, batch_id)
            if batch is None:
                return
            status = derive_batch_status(
                batch.status,
                repo.videos_of(session, batch_id),
                repo.clips_of_batch(session, batch_id),
            )
            if status != batch.status:
                batch.status = status
                session.commit()
                self.ctx.publish(batch_id, "batch", status=status)
