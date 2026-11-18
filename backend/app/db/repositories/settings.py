"""Key/value access to the `settings` table (one JSON value per top-level setting)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings_schema import Settings, merge_stored
from app.db.models import SettingRow


def load_all(session: Session) -> dict[str, Any]:
    """Return every stored setting as `{key: decoded JSON value}`.

    Raises:
        json.JSONDecodeError: if a stored value is not valid JSON.
    """
    rows = session.scalars(select(SettingRow)).all()
    return {row.key: json.loads(row.value_json) for row in rows}


def save_many(session: Session, values: Mapping[str, Any]) -> None:
    """Insert or replace the given keys. The caller commits."""
    for key, value in values.items():
        session.merge(SettingRow(key=key, value_json=json.dumps(value)))


def load_settings(session: Session) -> Settings:
    """Stored settings over the defaults, validated."""
    return Settings.model_validate(merge_stored(load_all(session)))
