from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.main import create_app

FRONTEND_ORIGIN = "http://localhost:3000"


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    """A config whose DATA_DIR is a fresh temp folder (path contains a space on purpose)."""
    return AppConfig(
        backend_host="127.0.0.1",
        backend_port=8000,
        allowed_origin=FRONTEND_ORIGIN,
        data_dir=tmp_path / "data dir",
        log_level="INFO",
        ffmpeg_path=None,
    )


@pytest.fixture
def client(config: AppConfig) -> Iterator[TestClient]:
    with TestClient(create_app(config), raise_server_exceptions=False) as test_client:
        yield test_client
