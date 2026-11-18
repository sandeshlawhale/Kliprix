"""Pick the adapter for a URL after the allowlist check."""

from __future__ import annotations

from collections.abc import Sequence

from app.config import AppConfig
from app.core.errors import AppError, ErrorCode
from app.sources.base import SourceAdapter
from app.sources.urls import host_matches, parse_allowed_url
from app.sources.youtube import YoutubeAdapter


def build_adapters(config: AppConfig) -> list[SourceAdapter]:
    """All adapters for this process (YouTube only in v1, D-021)."""
    return [YoutubeAdapter(config)]


def resolve(raw_url: str, adapters: Sequence[SourceAdapter]) -> tuple[SourceAdapter, str]:
    """Return the adapter for `raw_url` and the canonical URL it will fetch.

    Raises:
        AppError INVALID_URL / UNSUPPORTED_HOST: from the allowlist or the adapter.
    """
    parts = parse_allowed_url(raw_url)
    host = parts.hostname or ""
    for adapter in adapters:
        if any(host_matches(host, allowed) for allowed in adapter.hosts):
            return adapter, adapter.canonical_url(parts)
    raise AppError(ErrorCode.UNSUPPORTED_HOST, "This site is not supported yet.")
