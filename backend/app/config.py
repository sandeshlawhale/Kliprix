"""Backend settings read from environment variables, with documented defaults.

See docs/06-architecture.md ("Environment variables"). No secrets are read here:
API keys live in memory only and never pass through configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

APP_NAME = "Kliprix"
APP_VERSION = "0.1.0"

DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 8000
DEFAULT_ALLOWED_ORIGIN = "http://localhost:3000"


@dataclass(frozen=True)
class Settings:
    """Runtime settings for one backend process."""

    backend_host: str
    backend_port: int
    allowed_origin: str


def load_settings() -> Settings:
    """Build settings from the environment.

    Raises:
        ValueError: if BACKEND_PORT is not an integer.
    """
    return Settings(
        backend_host=os.environ.get("BACKEND_HOST", DEFAULT_BACKEND_HOST),
        backend_port=int(os.environ.get("BACKEND_PORT", str(DEFAULT_BACKEND_PORT))),
        allowed_origin=os.environ.get("ALLOWED_ORIGIN", DEFAULT_ALLOWED_ORIGIN),
    )
