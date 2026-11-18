"""`POST /api/llm/models` and `POST /api/llm/test` with the provider faked (tests only)."""

import logging
import os
import urllib.error
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.llm.models import list_models

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
import litellm.exceptions  # must come after the env var above

KEY = "sk-endpoint-test-SECRET-987654"


def ok_reply(**_: Any) -> Any:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))])


def test_list_models_per_provider() -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    def fetch(url: str, headers: dict[str, str]) -> Any:
        seen.append((url, headers))
        if "googleapis" in url:
            return {
                "models": [
                    {"name": "models/gemini-b", "displayName": "Gemini B",
                     "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]},
                ]
            }  # fmt: skip
        if "anthropic" in url:
            return {"data": [{"id": "claude-x", "display_name": "Claude X"}]}
        return {"data": [{"id": "z-model"}, {"id": "a-model", "name": "A"}]}

    gemini = list_models("gemini", KEY, None, fetch)
    assert [(m.id, m.label) for m in gemini] == [("gemini-b", "Gemini B")]
    assert seen[-1][1] == {"x-goog-api-key": KEY}
    assert KEY not in seen[-1][0]
    assert [m.id for m in list_models("anthropic", KEY, None, fetch)] == ["claude-x"]
    assert seen[-1][1]["x-api-key"] == KEY
    assert [m.id for m in list_models("openai", KEY, None, fetch)] == ["a-model", "z-model"]
    assert seen[-1][1] == {"Authorization": f"Bearer {KEY}"}
    list_models("openai_compatible", KEY, "http://127.0.0.1:1234/v1/", fetch)
    assert seen[-1][0] == "http://127.0.0.1:1234/v1/models"


def test_models_endpoint(client: TestClient) -> None:
    client.app.state.llm_fetch = lambda url, headers: {"data": [{"id": "gpt-x"}]}  # type: ignore[attr-defined]
    response = client.post("/api/llm/models", json={"provider": "openai", "api_key": KEY})
    assert response.status_code == 200
    assert response.json() == {"models": [{"id": "gpt-x", "label": "gpt-x"}]}


def test_models_endpoint_auth_failure(client: TestClient) -> None:
    def refuse(url: str, headers: dict[str, str]) -> Any:
        raise urllib.error.HTTPError(url, 401, "Unauthorized", None, None)  # type: ignore[arg-type]

    client.app.state.llm_fetch = refuse  # type: ignore[attr-defined]
    response = client.post("/api/llm/models", json={"provider": "anthropic", "api_key": KEY})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "LLM_AUTH_FAILED"
    assert KEY not in response.text


def test_models_endpoint_needs_base_url_for_compatible(client: TestClient) -> None:
    response = client.post(
        "/api/llm/models", json={"provider": "openai_compatible", "api_key": KEY}
    )
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_test_endpoint_ok(client: TestClient) -> None:
    client.app.state.llm_completion = ok_reply  # type: ignore[attr-defined]
    response = client.post(
        "/api/llm/test", json={"provider": "gemini", "model": "gemini-x", "api_key": KEY}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["latency_ms"] >= 0


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (
            litellm.exceptions.AuthenticationError("bad key " + KEY, "gemini", "m"),
            401,
            "LLM_AUTH_FAILED",
        ),
        (litellm.exceptions.NotFoundError("no model", "gemini", "m"), 502, "LLM_BAD_RESPONSE"),
    ],
)
def test_test_endpoint_errors_never_leak_key(
    client: TestClient, config: AppConfig, error: Exception, status: int, code: str
) -> None:
    def fail(**_: Any) -> Any:
        raise error

    client.app.state.llm_completion = fail  # type: ignore[attr-defined]
    response = client.post(
        "/api/llm/test", json={"provider": "gemini", "model": "m", "api_key": KEY}
    )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert KEY not in response.text
    for handler in logging.getLogger().handlers:
        handler.flush()
    log = (config.logs_dir / "app.log").read_text("utf-8")
    assert KEY not in log
    assert "llm call failed" in log


def test_test_endpoint_explains_retired_and_busy_models(client: TestClient) -> None:
    calls: list[int] = []

    def busy(**_: Any) -> Any:
        calls.append(1)
        raise litellm.exceptions.ServiceUnavailableError("high demand", "gemini", "m")

    client.app.state.llm_completion = busy  # type: ignore[attr-defined]
    body = {"provider": "gemini", "model": "m", "api_key": KEY}
    response = client.post("/api/llm/test", json=body)
    assert response.json()["error"]["code"] == "LLM_BAD_RESPONSE"
    assert "busy" in response.json()["error"]["message"]
    assert len(calls) == 1  # the Test button does not retry

    def retired(**_: Any) -> Any:
        raise litellm.exceptions.NotFoundError("no longer available", "gemini", "m")

    client.app.state.llm_completion = retired  # type: ignore[attr-defined]
    message = client.post("/api/llm/test", json=body).json()["error"]["message"]
    assert "not available for your API key" in message
