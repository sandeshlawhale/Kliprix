"""The LLM session: provider, model, and API key, held in process memory only.

Never persisted, logged, or returned by the API (hard rule 4). Restarting the backend
forgets it. Thread-safe because the job runner (M7) reads it from another thread.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from app.llm.providers import ProviderId


@dataclass(frozen=True)
class LlmSession:
    provider: ProviderId
    model: str
    api_key: str
    base_url: str | None

    def __repr__(self) -> str:
        # Keeps the key out of tracebacks and debug output.
        return f"LlmSession(provider={self.provider!r}, model={self.model!r}, api_key='***')"


class LlmSessionStore:
    """Holds at most one active session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: LlmSession | None = None

    def set(self, session: LlmSession) -> None:
        with self._lock:
            self._session = session

    def get(self) -> LlmSession | None:
        with self._lock:
            return self._session

    def clear(self) -> None:
        with self._lock:
            self._session = None

    def secrets(self) -> tuple[str, ...]:
        """Values the log redactor must hide."""
        session = self.get()
        return (session.api_key,) if session is not None else ()
