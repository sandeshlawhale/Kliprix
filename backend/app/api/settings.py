"""Settings endpoints (docs/07-api-contract.md, "Settings").

Stored as one row per top-level key; anything not stored falls back to the defaults
below (AA-012). PUT takes a partial body and merges it, `style_defaults` included.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import ValidationError

from app.api.deps import DbDep
from app.core.errors import ERROR_RESPONSES, AppError, ErrorCode
from app.core.settings_schema import (
    Settings,
    SettingsUpdate,
    merge_stored,
)
from app.db.repositories import settings as settings_repo

router = APIRouter(prefix="/api", tags=["settings"], responses=ERROR_RESPONSES)


def _current(db: DbDep) -> dict[str, Any]:
    return merge_stored(settings_repo.load_all(db))


@router.get("/settings")
def get_settings(db: DbDep) -> Settings:
    """Current settings (stored values over defaults)."""
    return Settings.model_validate(_current(db))


@router.put("/settings")
def put_settings(update: SettingsUpdate, db: DbDep) -> Settings:
    """Merge a partial update into the stored settings and return the result.

    Errors: VALIDATION_ERROR if a value is out of range or explicitly null.
    """
    changes = update.model_dump(exclude_unset=True)
    merged = _current(db)
    for key, value in changes.items():
        if key == "style_defaults" and isinstance(value, dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    try:
        result = Settings.model_validate(merged)
    except ValidationError as exc:
        errors = [
            {"loc": [str(part) for part in err["loc"]], "msg": err["msg"]} for err in exc.errors()
        ]
        raise AppError(
            ErrorCode.VALIDATION_ERROR, "These settings are not valid.", {"errors": errors}
        ) from exc
    dumped = result.model_dump()
    settings_repo.save_many(db, {key: dumped[key] for key in changes})
    db.commit()
    return result
