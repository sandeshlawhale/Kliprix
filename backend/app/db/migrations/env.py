"""Alembic environment.

Database URL: the `sqlalchemy.url` option when set (programmatic runs, tests), otherwise
the backend config (`DATA_DIR`), so `alembic upgrade head` from `backend/` just works.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from app.config import ensure_data_dirs, load_config
from app.db.engine import make_engine
from app.db.models import Base

config = context.config

# Only the CLI (alembic.ini) configures logging; programmatic runs keep the app's logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _db_url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    app_config = load_config()
    ensure_data_dirs(app_config)
    return app_config.db_url


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(_db_url())
    with engine.connect() as connection:
        # SQLite cannot ALTER most things in place; batch mode rebuilds tables instead.
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
