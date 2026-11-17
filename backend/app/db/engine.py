"""SQLAlchemy engine and session factory for the SQLite database."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import SQLITE_BUSY_TIMEOUT_MS


def _apply_pragmas(dbapi_connection: Any, _: Any) -> None:
    # Pragmas from docs/08: WAL lets the UI read while the job runner writes.
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={int(SQLITE_BUSY_TIMEOUT_MS)}")
    finally:
        cursor.close()


def make_engine(db_url: str) -> Engine:
    """Create an engine whose every connection has the documented pragmas."""
    # FastAPI runs sync endpoints in a thread pool, so connections cross threads.
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _apply_pragmas)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Sessions that keep loaded objects usable after commit."""
    return sessionmaker(engine, expire_on_commit=False)
