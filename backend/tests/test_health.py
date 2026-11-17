from fastapi.testclient import TestClient

from app.config import APP_VERSION
from tests.conftest import FRONTEND_ORIGIN


def test_health_returns_ok_and_version(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": APP_VERSION}


def test_cors_allows_only_configured_origin(client: TestClient) -> None:
    allowed = client.get("/api/health", headers={"Origin": FRONTEND_ORIGIN})
    assert allowed.headers.get("access-control-allow-origin") == FRONTEND_ORIGIN
    other = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_openapi_lists_health(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/health" in schema["paths"]
