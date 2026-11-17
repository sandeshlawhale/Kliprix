from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.engine import make_engine, make_session_factory
from app.db.migrate import upgrade_to_head
from app.db.models import Base, BatchRow, VideoRow
from app.db.repositories import settings as settings_repo

EXPECTED_TABLES = {"settings", "batches", "videos", "clips", "jobs", "alembic_version"}


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{(tmp_path / 'fresh db' / 'app.sqlite').as_posix()}"
    (tmp_path / "fresh db").mkdir()
    upgrade_to_head(url)
    return url


def test_migrations_create_documented_tables_and_indexes(db_url: str) -> None:
    inspector = inspect(make_engine(db_url))
    assert set(inspector.get_table_names()) == EXPECTED_TABLES
    indexes = {ix["name"] for t in ("videos", "clips", "jobs") for ix in inspector.get_indexes(t)}
    assert {
        "ix_videos_batch_id",
        "ix_clips_video_id",
        "ix_clips_batch_id",
        "ix_jobs_status_priority_created",
    } <= indexes


def test_migrations_match_models(db_url: str) -> None:
    with make_engine(db_url).connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


def test_upgrade_is_idempotent(db_url: str) -> None:
    upgrade_to_head(db_url)
    with make_engine(db_url).connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def test_connections_use_documented_pragmas(db_url: str) -> None:
    with make_engine(db_url).connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("PRAGMA busy_timeout")).scalar_one() == 5000


def test_foreign_keys_and_cascade(db_url: str) -> None:
    factory = make_session_factory(make_engine(db_url))
    with factory() as session:
        session.add(VideoRow(id="v1", batch_id="missing", position=0, url="u", status="queued"))
        with pytest.raises(IntegrityError):
            session.commit()
    with factory() as session:
        session.add(BatchRow(id="b1", status="queued", style_json="{}"))
        session.add(VideoRow(id="v1", batch_id="b1", position=0, url="u", status="queued"))
        session.commit()
        session.execute(text("DELETE FROM batches WHERE id = 'b1'"))
        session.commit()
        assert session.get(VideoRow, "v1") is None


def test_timestamps_round_trip_as_utc(db_url: str) -> None:
    factory = make_session_factory(make_engine(db_url))
    with factory() as session:
        session.add(BatchRow(id="b1", status="queued", style_json="{}"))
        session.commit()
    with factory() as session:
        row = session.get(BatchRow, "b1")
        assert row is not None
        assert row.created_at.tzinfo == UTC
        assert row.created_at <= datetime.now(UTC)


def test_settings_repository_round_trip(db_url: str) -> None:
    factory = make_session_factory(make_engine(db_url))
    with factory() as session:
        settings_repo.save_many(session, {"theme": "dark", "style_defaults": {"blur": 10}})
        session.commit()
        settings_repo.save_many(session, {"theme": "light"})
        session.commit()
    with factory() as session:
        assert settings_repo.load_all(session) == {
            "theme": "light",
            "style_defaults": {"blur": 10},
        }
