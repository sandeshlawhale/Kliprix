"""Structured, rotating file logging that never writes secrets.

Every formatted line, tracebacks included, passes through a redactor that replaces
the secrets currently held in memory (the LLM key) with `***`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from logging.handlers import RotatingFileHandler

from app.config import LOG_BACKUP_COUNT, LOG_FILE_NAME, LOG_MAX_BYTES, AppConfig

REDACTED = "***"
LOG_FORMAT = "%(asctime)s level=%(levelname)s logger=%(name)s %(message)s"

SecretSource = Callable[[], Iterable[str]]


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace every non-empty secret in `text` with `***`."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


class RedactingFormatter(logging.Formatter):
    """Formatter that removes known secrets from the final line."""

    def __init__(self, secrets: SecretSource) -> None:
        super().__init__(LOG_FORMAT)
        self._secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record), self._secrets())


class _OwnedFileHandler(RotatingFileHandler):
    """Marks the handler installed here, so a second setup replaces it."""


class _OwnedConsoleHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Marks the handler installed here, so a second setup replaces it."""


def setup_logging(config: AppConfig, secrets: SecretSource) -> None:
    """Send logs to `DATA_DIR/logs/app.log` (rotating) and to the console.

    Expects `config.logs_dir` to exist. Calling it again replaces the handlers it added.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, _OwnedFileHandler | _OwnedConsoleHandler):
            root.removeHandler(handler)
            handler.close()
    formatter = RedactingFormatter(secrets)
    file_handler = _OwnedFileHandler(
        config.logs_dir / LOG_FILE_NAME,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    console_handler = _OwnedConsoleHandler()
    for handler in (file_handler, console_handler):
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(config.log_level)
