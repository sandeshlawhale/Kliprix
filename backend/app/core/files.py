"""File helpers shared by pipeline stages."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pydantic import BaseModel


def write_json_atomic(path: Path, model: BaseModel) -> None:
    """Write `model` as UTF-8 JSON to `path` via a temp file and rename (docs/11 "Reliability").

    A crash mid-write leaves either the old file or no file, never a half-written one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(model.model_dump_json())
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
