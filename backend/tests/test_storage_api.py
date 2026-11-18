"""Storage usage and user-triggered cleanups."""

from fastapi.testclient import TestClient

from app.config import AppConfig


def test_storage_usage_and_warning(client: TestClient, config: AppConfig) -> None:
    (config.data_dir / "exports" / "b").mkdir(parents=True)
    (config.data_dir / "exports" / "b" / "clip.mp4").write_bytes(b"x" * 1000)
    (config.data_dir / "models" / "m.bin").write_bytes(b"y" * 500)
    info = client.get("/api/storage").json()
    assert info["folders"]["exports"] == 1000
    assert info["folders"]["models"] == 500
    assert info["total_bytes"] == 1500
    assert info["warn"] is False
    client.put("/api/settings", json={"storage_warn_gb": 0.000001})
    assert client.get("/api/storage").json()["warn"] is True


def test_cleanup_validation(client: TestClient) -> None:
    missing = client.post("/api/storage/cleanup", json={"scope": "batch"})
    assert missing.json()["error"]["code"] == "VALIDATION_ERROR"
    unknown = client.post("/api/storage/cleanup", json={"scope": "batch", "batch_id": "nope"})
    assert unknown.json()["error"]["code"] == "NOT_FOUND"
    assert client.post("/api/storage/cleanup", json={"scope": "previews"}).json() == {
        "freed_bytes": 0
    }
