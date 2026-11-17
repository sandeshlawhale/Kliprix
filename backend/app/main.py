"""FastAPI application factory.

Start with: `uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000`
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.telemetry import TelemetryConfig

from app.api import system
from app.config import APP_NAME, APP_VERSION, Settings, load_settings

# Local-only app with a no-telemetry rule: switch off FastAPI's native OpenTelemetry hooks.
_TELEMETRY_OFF: TelemetryConfig = {
    "tracing": False,
    "metrics": False,
    "logs": False,
    "operation_spans": False,
    "auto_configure": False,
}


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the backend app with its routers and CORS policy."""
    active = settings if settings is not None else load_settings()
    app = FastAPI(title=APP_NAME, version=APP_VERSION, telemetry=_TELEMETRY_OFF)
    # The browser normally reaches us through the Next.js proxy (same origin);
    # CORS only admits the configured frontend origin for direct calls.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[active.allowed_origin],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.include_router(system.router)
    return app
