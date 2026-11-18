"""List the models a key can use, live from each provider (docs/10 "Provider setup").

Model lists are never hard-coded (they go stale). The key is sent in a header, never in the
URL. Any failure maps to LLM_AUTH_FAILED as the API contract says; the user can still type
a model id by hand.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.config import LLM_LIST_TIMEOUT_S
from app.core.errors import AppError, ErrorCode
from app.llm.providers import ProviderId

logger = logging.getLogger(__name__)

GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"
ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models?limit=1000"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
# Gemini lists embedding and other models too; only these can write text.
GEMINI_TEXT_METHOD = "generateContent"

# Fetches a URL with headers and returns the parsed JSON body (replaceable in tests).
Fetch = Callable[[str, dict[str, str]], Any]


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str


def fetch_json(url: str, headers: dict[str, str]) -> Any:
    """GET `url` and parse JSON. Raises urllib/JSON errors to the caller."""
    request = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310 - fixed https URLs or the user's own base URL
    with urllib.request.urlopen(request, timeout=LLM_LIST_TIMEOUT_S) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _gemini(body: Any) -> list[ModelInfo]:
    models = []
    for item in body.get("models", []):
        if GEMINI_TEXT_METHOD not in item.get("supportedGenerationMethods", []):
            continue
        model_id = str(item["name"]).removeprefix("models/")
        models.append(ModelInfo(model_id, str(item.get("displayName") or model_id)))
    return models


def _anthropic(body: Any) -> list[ModelInfo]:
    return [
        ModelInfo(str(item["id"]), str(item.get("display_name") or item["id"]))
        for item in body.get("data", [])
    ]


def _openai_like(body: Any) -> list[ModelInfo]:
    return [
        ModelInfo(str(item["id"]), str(item.get("name") or item["id"]))
        for item in body.get("data", [])
    ]


def list_models(
    provider: ProviderId,
    api_key: str,
    base_url: str | None,
    fetch: Fetch = fetch_json,
) -> list[ModelInfo]:
    """Models available to `api_key`, sorted by id.

    Raises:
        AppError LLM_AUTH_FAILED: the provider refused or the list could not be read.
    """
    try:
        if provider == "gemini":
            models = _gemini(fetch(GEMINI_MODELS_URL, {"x-goog-api-key": api_key}))
        elif provider == "anthropic":
            headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
            models = _anthropic(fetch(ANTHROPIC_MODELS_URL, headers))
        else:
            if provider == "openai":
                url = OPENAI_MODELS_URL
            elif provider == "openrouter":
                url = OPENROUTER_MODELS_URL
            else:
                if not base_url:
                    raise AppError(ErrorCode.VALIDATION_ERROR, "This provider needs a base URL.")
                url = base_url.rstrip("/") + "/models"
            models = _openai_like(fetch(url, {"Authorization": f"Bearer {api_key}"}))
    except AppError:
        raise
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        # HTTPError is a URLError; its text never includes the key (headers are not echoed).
        logger.warning("listing models failed provider=%s: %s", provider, type(exc).__name__)
        raise AppError(
            ErrorCode.LLM_AUTH_FAILED,
            "Could not list models with this key. Check the key, or type a model id.",
            {"provider_error": type(exc).__name__},
        ) from exc
    return sorted(models, key=lambda model: model.id)
