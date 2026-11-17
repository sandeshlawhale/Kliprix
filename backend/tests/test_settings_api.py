from fastapi.testclient import TestClient

from app.config import AppConfig
from app.main import create_app


def test_defaults_on_fresh_db(client: TestClient) -> None:
    body = client.get("/api/settings").json()
    assert body == {
        "theme": "system",
        "style_defaults": {
            "caption_style": "pop",
            "caption_language": "en",
            "clip_length_mode": "standard",
            "clips_per_video": 5,
            "blur": 30,
            "crop_mode": "auto",
            "show_title": True,
        },
        "whisper_model": "auto",
        "source_policy": "ask",
        "storage_warn_gb": 10.0,
        "thorough_mode": False,
    }


def test_partial_update_merges_and_survives_restart(config: AppConfig) -> None:
    with TestClient(create_app(config)) as client:
        response = client.put(
            "/api/settings", json={"theme": "dark", "style_defaults": {"blur": 12}}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["theme"] == "dark"
        assert body["style_defaults"]["blur"] == 12
        assert body["style_defaults"]["caption_style"] == "pop"
    # A new app on the same DATA_DIR is a backend restart.
    with TestClient(create_app(config)) as restarted:
        again = restarted.get("/api/settings").json()
        assert again["theme"] == "dark"
        assert again["style_defaults"]["blur"] == 12


def test_invalid_values_are_rejected(client: TestClient) -> None:
    for payload in (
        {"style_defaults": {"clips_per_video": 6}},
        {"style_defaults": {"blur": 61}},
        {"theme": "neon"},
        {"storage_warn_gb": 0},
        {"unknown_field": 1},
        {"theme": None},
    ):
        response = client.put("/api/settings", json=payload)
        assert response.status_code == 422, payload
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert client.get("/api/settings").json()["theme"] == "system"
