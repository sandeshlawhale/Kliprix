"""LLM session endpoints (docs/07-api-contract.md, "LLM session").

`POST /api/llm/models` and `POST /api/llm/test` call the provider and arrive with the
LLM client in M5; this router only manages the in-memory session.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr

from app.core.errors import ERROR_RESPONSES, AppError, ErrorCode
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


@router.put("/session")
def put_session(body: SessionRequest, store: StoreDep) -> SessionConfigured:
    """Keep provider, model, and key in memory for this backend process.

    Errors: VALIDATION_ERROR if the key or model is blank, or base_url is missing for
    `openai_compatible` / given for a provider that does not use it.
    """
    api_key = body.api_key.get_secret_value().strip()
    model = body.model.strip()
    if not api_key or not model:
        raise AppError(ErrorCode.VALIDATION_ERROR, "Model and API key must not be blank.")
    needs_base_url = PROVIDERS_BY_ID[body.provider].needs_base_url
    if needs_base_url and body.base_url is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "This provider needs a base URL.")
    if not needs_base_url and body.base_url is not None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "This provider does not use a base URL.")
    store.set(
        LlmSession(
            provider=body.provider,
            model=model,
            api_key=api_key,
            base_url=str(body.base_url) if body.base_url is not None else None,
        )
    )
    return SessionConfigured(configured=True, provider=body.provider, model=model)


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
