"""System endpoints (docs/07-api-contract.md, "System")."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import APP_VERSION

router = APIRouter(prefix="/api", tags=["system"])


class HealthResponse(BaseModel):
    """Liveness answer: the process is up and serving requests."""

    status: Literal["ok"]
    version: str


@router.get("/health")
def get_health() -> HealthResponse:
    """Return `{status: "ok", version}` while the backend is running."""
    return HealthResponse(status="ok", version=APP_VERSION)
