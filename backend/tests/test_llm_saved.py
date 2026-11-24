"""Saved LLM setup: "Save" keeps provider, model, and the key encrypted in the DB (D-053)."""

import logging
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.core.secret_box import SECRET_FILE_NAME, SecretBox, SecretBoxError
from app.main import create_app

KEY = "sk-test-not-a-real-key-saved-987654"
BODY = {"provider": "gemini", "model": "gemini-x", "api_key": KEY}


def _restart(config: AppConfig) -> TestClient:
    return TestClient(create_app(config), raise_server_exceptions=False)


def _db_rows(config: AppConfig) -> list[tuple[str, str, str | None, str]]:
    with sqlite3.connect(config.db_path) as conn:
        return conn.execute(
            "SELECT provider, model, base_url, api_key_enc FROM llm_credentials"
        ).fetchall()


def test_save_encrypts_key_and_reports_saved(client: TestClient, config: AppConfig) -> None:
    response = client.put("/api/llm/saved", json=BODY)
    assert response.status_code == 200
    saved = {"provider": "gemini", "model": "gemini-x"}
    assert response.json() == {
        "configured": True,
        "provider": "gemini",
        "model": "gemini-x",
        "saved": saved,
    }
    assert client.get("/api/llm/session").json()["saved"] == saved
    assert KEY not in response.text
    rows = _db_rows(config)
    assert len(rows) == 1
    provider, model, base_url, token = rows[0]
    assert (provider, model, base_url) == ("gemini", "gemini-x", None)
    assert KEY not in token
    assert SecretBox(config.data_dir).decrypt(token) == KEY
    # The plain key is nowhere in DATA_DIR (database, WAL, logs, key file).
    logging.getLogger().handlers[0].flush()
    for path in config.data_dir.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes(), path


def test_saved_setup_survives_a_restart(client: TestClient, config: AppConfig) -> None:
    client.put("/api/llm/saved", json=BODY)
    client.close()
    with _restart(config) as again:
        status = again.get("/api/llm/session").json()
        assert status["configured"] is True
        assert (status["provider"], status["model"]) == ("gemini", "gemini-x")
        store = again.app.state.llm_session  # type: ignore[attr-defined]
        assert store.get().api_key == KEY


def test_saving_again_replaces_the_saved_setup(client: TestClient, config: AppConfig) -> None:
    client.put("/api/llm/saved", json=BODY)
    client.put(
        "/api/llm/saved",
        json={
            "provider": "openai_compatible",
            "model": "local-m",
            "api_key": "other-key",
            "base_url": "http://127.0.0.1:1234/v1",
        },
    )
    rows = _db_rows(config)
    assert [r[:3] for r in rows] == [("openai_compatible", "local-m", "http://127.0.0.1:1234/v1")]
    client.close()
    with _restart(config) as again:
        session = again.app.state.llm_session.get()  # type: ignore[attr-defined]
        assert (session.api_key, session.base_url) == ("other-key", "http://127.0.0.1:1234/v1")


def test_session_only_is_not_saved(client: TestClient, config: AppConfig) -> None:
    client.put("/api/llm/session", json=BODY)
    assert client.get("/api/llm/session").json()["saved"] is None
    assert _db_rows(config) == []
    client.close()
    with _restart(config) as again:
        assert again.get("/api/llm/session").json()["configured"] is False


def test_delete_saved_keeps_the_running_session(client: TestClient, config: AppConfig) -> None:
    client.put("/api/llm/saved", json=BODY)
    assert client.delete("/api/llm/saved").status_code == 204
    status = client.get("/api/llm/session").json()
    assert status["configured"] is True
    assert status["saved"] is None
    assert _db_rows(config) == []
    client.close()
    with _restart(config) as again:
        assert again.get("/api/llm/session").json()["configured"] is False


def test_clear_session_keeps_the_saved_setup(client: TestClient) -> None:
    client.put("/api/llm/saved", json=BODY)
    assert client.delete("/api/llm/session").status_code == 204
    status = client.get("/api/llm/session").json()
    assert status["configured"] is False
    assert status["saved"] == {"provider": "gemini", "model": "gemini-x"}


def test_save_validates_like_session(client: TestClient, config: AppConfig) -> None:
    for payload in (
        {"provider": "gemini", "model": " ", "api_key": KEY},
        {"provider": "openai_compatible", "model": "m", "api_key": KEY},
        {"provider": "gemini", "model": "m", "api_key": KEY, "extra": 1},
    ):
        response = client.put("/api/llm/saved", json=payload)
        assert response.status_code == 422
        assert KEY not in response.text
    assert _db_rows(config) == []


def test_lost_key_file_drops_the_saved_setup(
    client: TestClient, config: AppConfig, caplog: pytest.LogCaptureFixture
) -> None:
    client.put("/api/llm/saved", json=BODY)
    client.close()
    (config.data_dir / SECRET_FILE_NAME).unlink()
    with caplog.at_level(logging.WARNING), _restart(config) as again:
        status = again.get("/api/llm/session").json()
        assert status == {"configured": False, "provider": None, "model": None, "saved": None}
    assert "cannot be decrypted" in caplog.text
    assert _db_rows(config) == []


def test_secret_box_round_trip_and_wrong_key(tmp_path: object) -> None:
    from pathlib import Path

    base = Path(str(tmp_path))
    box = SecretBox(base / "a")
    token = box.encrypt("hello")
    assert box.decrypt(token) == "hello"
    assert SecretBox(base / "a").decrypt(token) == "hello"  # same key file
    raw = (base / "a" / SECRET_FILE_NAME).read_bytes()
    if sys.platform == "win32":
        assert raw.startswith(b"KLPX-DPAPI1:")  # wrapped for this Windows user
    other = SecretBox(base / "b")
    other.encrypt("x")
    with pytest.raises(SecretBoxError):
        other.decrypt(token)
    with pytest.raises(SecretBoxError):
        SecretBox(base / "missing").decrypt(token)
