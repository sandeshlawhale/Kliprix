"""LLM session endpoints (docs/07-api-contract.md, "LLM session").

`POST /api/llm/models` lists models live from the provider; `POST /api/llm/test` makes one
tiny completion. The session itself lives in memory only.
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr

from app.core.errors import ERROR_RESPONSES, AppError, ErrorCode
from app.llm.client import LlmClient
from app.llm.models import list_models
from app.llm.providers import PROVIDERS, PROVIDERS_BY_ID, ProviderId
from app.llm.session import LlmSession, LlmSessionStore

router = APIRouter(prefix="/api/llm", tags=["llm"], responses=ERROR_RESPONSES)

MAX_MODEL_ID_LENGTH = 200
MAX_API_KEY_LENGTH = 1000


def get_store(request: Request) -> LlmSessionStore:
    store: LlmSessionStore = request.app.state.llm_session
    return store


StoreDep = Annotated[LlmSessionStore, Depends(get_store)]


class ProviderInfo(BaseModel):
    id: ProviderId
    label: str
    needs_base_url: bool


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderId
    model: str = Field(min_length=1, max_length=MAX_MODEL_ID_LENGTH)
    api_key: SecretStr = Field(min_length=1, max_length=MAX_API_KEY_LENGTH)
    base_url: HttpUrl | None = None


class SessionConfigured(BaseModel):
    configured: bool
    provider: ProviderId
    model: str


class SessionStatus(BaseModel):
    """Whether a session is active. Never includes the key."""

    configured: bool
    provider: ProviderId | None = None
    model: str | None = None


@router.get("/providers")
def list_providers() -> list[ProviderInfo]:
    """The providers the user can pick."""
    return [
        ProviderInfo(id=p.id, label=p.label, needs_base_url=p.needs_base_url) for p in PROVIDERS
    ]


class ModelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderId
    api_key: SecretStr = Field(min_length=1, max_length=MAX_API_KEY_LENGTH)
    base_url: HttpUrl | None = None


class ModelEntry(BaseModel):
    id: str
    label: str


class ModelsResponse(BaseModel):
    models: list[ModelEntry]


class TestResponse(BaseModel):
    ok: bool
    latency_ms: int


def _session_from(
    provider: ProviderId, model: str, api_key: SecretStr, base_url: HttpUrl | None
) -> LlmSession:
    """Validated session values.

    Errors: VALIDATION_ERROR if the key or model is blank, or base_url is missing for
    `openai_compatible` / given for a provider that does not use it.
    """
    key = api_key.get_secret_value().strip()
    model = model.strip()
    if not key or not model:
        raise AppError(ErrorCode.VALIDATION_ERROR, "Model and API key must not be blank.")
    _check_base_url(provider, base_url)
    return LlmSession(
        provider=provider,
        model=model,
        api_key=key,
        base_url=str(base_url) if base_url is not None else None,
    )


def _check_base_url(provider: ProviderId, base_url: HttpUrl | None) -> None:
    needs_base_url = PROVIDERS_BY_ID[provider].needs_base_url
    if needs_base_url and base_url is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "This provider needs a base URL.")
    if not needs_base_url and base_url is not None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "This provider does not use a base URL.")


@router.post("/models")
def post_models(body: ModelsRequest, request: Request) -> ModelsResponse:
    """Models the key can use, fetched live from the provider.

    Errors: LLM_AUTH_FAILED when the provider refuses or cannot be reached.
    """
    key = body.api_key.get_secret_value().strip()
    if not key:
        raise AppError(ErrorCode.VALIDATION_ERROR, "The API key must not be blank.")
    _check_base_url(body.provider, body.base_url)
    base_url = str(body.base_url) if body.base_url is not None else None
    models = list_models(body.provider, key, base_url, request.app.state.llm_fetch)
    return ModelsResponse(models=[ModelEntry(id=m.id, label=m.label) for m in models])


@router.post("/test")
def post_test(body: SessionRequest, request: Request) -> TestResponse:
    """One tiny completion with these settings; reports the round-trip time.

    Errors: LLM_AUTH_FAILED, LLM_RATE_LIMITED, LLM_BAD_RESPONSE.
    """
    session = _session_from(body.provider, body.model, body.api_key, body.base_url)
    client = LlmClient(session, completion=request.app.state.llm_completion)
    started = time.monotonic()
    client.ping()
    return TestResponse(ok=True, latency_ms=round((time.monotonic() - started) * 1000))


@router.put("/session")
def put_session(body: SessionRequest, store: StoreDep) -> SessionConfigured:
    """Keep provider, model, and key in memory for this backend process.

    Errors: VALIDATION_ERROR if the key or model is blank, or base_url is missing for
    `openai_compatible` / given for a provider that does not use it.
    """
    session = _session_from(body.provider, body.model, body.api_key, body.base_url)
    store.set(session)
    return SessionConfigured(configured=True, provider=session.provider, model=session.model)


@router.get("/session")
def get_session(store: StoreDep) -> SessionStatus:
    """The active provider and model, if any."""
    session = store.get()
    if session is None:
        return SessionStatus(configured=False)
    return SessionStatus(configured=True, provider=session.provider, model=session.model)


@router.delete("/session", status_code=204)
def delete_session(store: StoreDep) -> Response:
    """Forget the session (and its key)."""
    store.clear()
    return Response(status_code=204)
