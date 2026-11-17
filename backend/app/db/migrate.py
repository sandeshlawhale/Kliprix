"""Run Alembic migrations from code (used at backend startup and in tests)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(db_url: str) -> Config:
    """Alembic config pointing at our migrations folder and the given database."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


def upgrade_to_head(db_url: str) -> None:
    """Apply every pending migration. Safe to call on an up-to-date database."""
    command.upgrade(alembic_config(db_url), "head")
