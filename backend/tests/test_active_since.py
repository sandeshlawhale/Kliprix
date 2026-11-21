"""`generation_started_at`: when the work now running for a video began (batch page timer)."""

from datetime import timedelta
from pathlib import Path

from app.db.engine import make_engine, make_session_factory
from app.db.migrate import upgrade_to_head
from app.db.models import JobRow, utc_now
from app.db.repositories import batches as repo


def _factory(tmp_path: Path):  # type: ignore[no-untyped-def]
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    upgrade_to_head(url)
    return make_session_factory(make_engine(url))


def test_timer_covers_pipeline_and_its_clips_then_stops(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        batch = repo.create_batch(session, ["https://youtu.be/x"], {}, None)
        video = repo.videos_of(session, batch.id)[0]
        job = session.query(JobRow).filter_by(ref_id=video.id).one()
        started = job.created_at
        assert repo.generation_started_at(session, video.id, []) == started

        # Pipeline done; its clip renders were queued right away: still the same run.
        job.status = "done"
        job.finished_at = utc_now()
        clip_job = repo.add_job(session, "clip_render", "c1", repo.PRIORITY_BACKGROUND)
        assert repo.generation_started_at(session, video.id, ["c1"]) == started

        # Everything finished: no timer.
        clip_job.status = "done"
        assert repo.generation_started_at(session, video.id, ["c1"]) is None

        # A re-render from the editor much later starts its own run.
        later = repo.add_job(session, "clip_preview", "c1", repo.PRIORITY_USER)
        later.created_at = job.finished_at + timedelta(minutes=5)
        assert repo.generation_started_at(session, video.id, ["c1"]) == later.created_at
