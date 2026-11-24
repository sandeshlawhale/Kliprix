import logging

from fastapi.testclient import TestClient

from app.config import AppConfig

KEY = "sk-test-not-a-real-key-123456"


def test_providers_list(client: TestClient) -> None:
    providers = client.get("/api/llm/providers").json()
    assert [p["id"] for p in providers] == [
        "gemini",
        "anthropic",
        "openai",
        "openrouter",
        "openai_compatible",
    ]
    assert [p["id"] for p in providers if p["needs_base_url"]] == ["openai_compatible"]


def test_session_lifecycle_never_returns_key(client: TestClient) -> None:
    assert client.get("/api/llm/session").json() == {
        "configured": False,
        "provider": None,
        "model": None,
        "saved": None,
    }
    put = client.put(
        "/api/llm/session", json={"provider": "gemini", "model": "gemini-x", "api_key": KEY}
    )
    assert put.status_code == 200
    expected = {"configured": True, "provider": "gemini", "model": "gemini-x", "saved": None}
    assert put.json() == expected
    status = client.get("/api/llm/session")
    assert status.json() == expected
    assert KEY not in put.text + status.text
    assert client.delete("/api/llm/session").status_code == 204
    assert client.get("/api/llm/session").json()["configured"] is False


def test_base_url_rules(client: TestClient) -> None:
    missing = client.put(
        "/api/llm/session",
        json={"provider": "openai_compatible", "model": "m", "api_key": KEY},
    )
    assert missing.status_code == 422
    unused = client.put(
        "/api/llm/session",
        json={"provider": "openai", "model": "m", "api_key": KEY, "base_url": "http://x.test"},
    )
    assert unused.status_code == 422
    ok = client.put(
        "/api/llm/session",
        json={
            "provider": "openai_compatible",
            "model": "m",
            "api_key": KEY,
            "base_url": "http://127.0.0.1:11434/v1",
        },
    )
    assert ok.status_code == 200


def test_invalid_requests_do_not_echo_key(client: TestClient) -> None:
    for payload in (
        {"provider": "nope", "model": "m", "api_key": KEY},
        {"provider": "gemini", "model": "m", "api_key": KEY, "extra": KEY},
        {"provider": "gemini", "model": " ", "api_key": KEY},
    ):
        response = client.put("/api/llm/session", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert KEY not in response.text


def test_key_never_reaches_disk(client: TestClient, config: AppConfig) -> None:
    client.put("/api/llm/session", json={"provider": "gemini", "model": "m", "api_key": KEY})
    logging.getLogger("test").error("accidentally logging %s", KEY)
    for handler in logging.getLogger().handlers:
        handler.flush()
    for path in config.data_dir.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes(), path
    assert "accidentally logging ***" in (config.logs_dir / "app.log").read_text("utf-8")
