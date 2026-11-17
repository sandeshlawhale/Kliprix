from fastapi.testclient import TestClient

from app.config import APP_VERSION, Settings
from app.main import create_app

FRONTEND_ORIGIN = "http://localhost:3000"


def _client() -> TestClient:
    settings = Settings(backend_host="127.0.0.1", backend_port=8000, allowed_origin=FRONTEND_ORIGIN)
    return TestClient(create_app(settings))


def test_health_returns_ok_and_version() -> None:
    response = _client().get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": APP_VERSION}


def test_cors_allows_only_configured_origin() -> None:
    client = _client()
    allowed = client.get("/api/health", headers={"Origin": FRONTEND_ORIGIN})
    assert allowed.headers.get("access-control-allow-origin") == FRONTEND_ORIGIN
    other = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_openapi_lists_health() -> None:
    schema = _client().get("/openapi.json").json()
    assert "/api/health" in schema["paths"]
