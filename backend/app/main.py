"""FastAPI application factory.

Start with: `uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000`
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.telemetry import TelemetryConfig

from app.api import batches, clips, llm, settings, storage, system, videos
from app.config import (
    APP_NAME,
    APP_VERSION,
    PARAKEET_MAX_THREADS,
    AppConfig,
    ensure_data_dirs,
    load_config,
)
from app.core.errors import install_error_handlers
from app.core.events import EventBus
from app.core.hardware import nvidia_vram_gb, speech_threads
from app.core.logging import setup_logging
from app.db.engine import make_engine, make_session_factory
from app.db.migrate import upgrade_to_head
from app.jobs.context import JobContext, ParakeetFactory
from app.jobs.runner import JobRunner
from app.llm.client import default_completion
from app.llm.models import fetch_json
from app.llm.session import LlmSessionStore
from app.pipeline.transcribe import RunnerFactory, WhisperRunner
from app.pipeline.transcribe_parakeet import ParakeetRunner
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


def _whisper_factory(config: AppConfig) -> Callable[[str, bool], RunnerFactory]:
    def factory(model: str, use_gpu: bool) -> RunnerFactory:
        return lambda: WhisperRunner(model, config.data_dir / "models", use_gpu)

    return factory


def _parakeet_factory(config: AppConfig) -> ParakeetFactory:
    def factory(
        cancel: threading.Event | None, on_download: Callable[[float], None]
    ) -> RunnerFactory:
        return lambda: ParakeetRunner(
            config.data_dir / "models",
            speech_threads(PARAKEET_MAX_THREADS),
            cancel=cancel,
            on_download=on_download,
        )

    return factory


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    runner: JobRunner = app.state.job_runner
    runner.start()
    try:
        yield
    finally:
        runner.stop()


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Create the backend app: data folders, logging, error handlers, routers, CORS."""
    active = config if config is not None else load_config()
    ensure_data_dirs(active)
    llm_session = LlmSessionStore()
    setup_logging(active, secrets=llm_session.secrets)
    # Migrations run on every start (no-op when current) so a fresh DATA_DIR just works.
    upgrade_to_head(active.db_url)
    app = FastAPI(title=APP_NAME, version=APP_VERSION, telemetry=_TELEMETRY_OFF, lifespan=_lifespan)
    app.state.config = active
    app.state.llm_session = llm_session
    # Provider calls; tests replace these two with fakes (the LLM is mocked in tests only).
    app.state.llm_completion = default_completion
    app.state.llm_fetch = fetch_json
    app.state.source_adapters = build_adapters(active)
    app.state.session_factory = make_session_factory(make_engine(active.db_url))
    app.state.event_bus = EventBus()
    # The job runner thread and what its jobs need (tests swap in fakes before starting).
    app.state.job_context = JobContext(
        config=active,
        session_factory=app.state.session_factory,
        adapters=app.state.source_adapters,
        llm=llm_session,
        bus=app.state.event_bus,
        whisper_factory=_whisper_factory(active),
        gpu_vram=nvidia_vram_gb,
        parakeet_factory=_parakeet_factory(active),
    )
    app.state.job_runner = JobRunner(app.state.job_context)
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
    app.include_router(batches.router)
    app.include_router(clips.router)
    app.include_router(storage.router)
    logger.info("backend ready version=%s", APP_VERSION)
    return app
