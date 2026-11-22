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
        # Queued, not picked up yet: no timer.
        assert repo.generation_started_at(session, video.id, []) is None

        job.status = "running"
        job.started_at = started = utc_now()
        assert repo.generation_started_at(session, video.id, []) == started

        # Pipeline done; its clip renders were queued right away: still the same run.
        job.status = "done"
        job.finished_at = utc_now()
        clip_job = repo.add_job(session, "clip_render", "c1", repo.PRIORITY_BACKGROUND)
        assert repo.generation_started_at(session, video.id, ["c1"]) == started

        # Everything finished: no timer.
        clip_job.status = "done"
        assert repo.generation_started_at(session, video.id, ["c1"]) is None

        # A re-render from the editor much later starts its own run, once it starts.
        later = repo.add_job(session, "clip_preview", "c1", repo.PRIORITY_USER)
        later.created_at = job.finished_at + timedelta(minutes=5)
        assert repo.generation_started_at(session, video.id, ["c1"]) is None
        later.status = "running"
        later.started_at = later.created_at + timedelta(seconds=3)
        assert repo.generation_started_at(session, video.id, ["c1"]) == later.started_at


def test_each_video_of_a_batch_starts_its_own_timer(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    with factory() as session:
        batch = repo.create_batch(session, ["https://youtu.be/a", "https://youtu.be/b"], {}, None)
        first, second = repo.videos_of(session, batch.id)
        job = session.query(JobRow).filter_by(ref_id=first.id).one()
        job.status = "running"
        job.started_at = utc_now()
        assert repo.generation_started_at(session, first.id, []) == job.started_at
        assert repo.generation_started_at(session, second.id, []) is None
