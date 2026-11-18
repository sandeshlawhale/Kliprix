"""FastAPI application factory.

Start with: `uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000`
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.telemetry import TelemetryConfig

from app.api import llm, settings, system, videos
from app.config import APP_NAME, APP_VERSION, AppConfig, ensure_data_dirs, load_config
from app.core.errors import install_error_handlers
from app.core.logging import setup_logging
from app.db.engine import make_engine, make_session_factory
from app.db.migrate import upgrade_to_head
from app.llm.session import LlmSessionStore
from app.sources.registry import build_adapters

logger = logging.getLogger(__name__)

# Local-only app with a no-telemetry rule: switch off FastAPI's native OpenTelemetry hooks.
_TELEMETRY_OFF: TelemetryConfig = {
    "tracing": False,
    "metrics": False,
    "logs": False,
    "operation_spans": False,
    "auto_configure": False,
}


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Create the backend app: data folders, logging, error handlers, routers, CORS."""
    active = config if config is not None else load_config()
    ensure_data_dirs(active)
    llm_session = LlmSessionStore()
    setup_logging(active, secrets=llm_session.secrets)
    # Migrations run on every start (no-op when current) so a fresh DATA_DIR just works.
    upgrade_to_head(active.db_url)
    app = FastAPI(title=APP_NAME, version=APP_VERSION, telemetry=_TELEMETRY_OFF)
    app.state.config = active
    app.state.llm_session = llm_session
    app.state.source_adapters = build_adapters(active)
    app.state.session_factory = make_session_factory(make_engine(active.db_url))
    install_error_handlers(app)
    # The browser normally reaches us through the Next.js proxy (same origin);
    # CORS only admits the configured frontend origin for direct calls.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[active.allowed_origin],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.include_router(system.router)
    app.include_router(settings.router)
    app.include_router(llm.router)
    app.include_router(videos.router)
    logger.info("backend ready version=%s", APP_VERSION)
    return app
