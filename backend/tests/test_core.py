import logging
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import DATA_SUBDIRS, REPO_ROOT, AppConfig, load_config, resolve_data_dir
from app.core.errors import AppError, ErrorCode, install_error_handlers
from app.core.logging import QUIET_LIBRARIES, RedactingFormatter, redact, setup_logging


def test_relative_data_dir_is_relative_to_repo_root() -> None:
    assert resolve_data_dir("./data") == (REPO_ROOT / "data").resolve()
    assert resolve_data_dir("") == REPO_ROOT / "data"


def test_load_config_rejects_unknown_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "LOUD")
    with pytest.raises(ValueError, match="LOG_LEVEL"):
        load_config()


def test_startup_creates_data_folders_and_log(client: TestClient, config: AppConfig) -> None:
    for name in DATA_SUBDIRS:
        assert (config.data_dir / name).is_dir()
    assert (config.logs_dir / "app.log").is_file()


def _app_raising(exc: Exception) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise exc

    @app.get("/typed/{number}")
    def typed(number: int) -> int:
        return number

    return TestClient(app, raise_server_exceptions=False)


def test_app_error_uses_documented_shape() -> None:
    client = _app_raising(AppError(ErrorCode.CONFLICT, "Already running.", {"id": "x"}))
    response = client.get("/boom")
    assert response.status_code == 409
    assert response.json() == {
        "error": {"code": "CONFLICT", "message": "Already running.", "details": {"id": "x"}}
    }


def test_unexpected_error_is_internal_without_detail() -> None:
    response = _app_raising(RuntimeError("secret path C:/x")).get("/boom")
    assert response.status_code == 500
    body = response.json()["error"]
    assert body["code"] == "INTERNAL"
    assert "C:/x" not in body["message"]


def test_validation_and_not_found_errors_use_shape() -> None:
    client = _app_raising(RuntimeError())
    invalid = client.get("/typed/abc")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "abc" not in invalid.text  # rejected input is never echoed
    missing = client.get("/nope")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


def test_redact_replaces_secrets() -> None:
    assert redact("key=sk-123 again sk-123", ["sk-123", ""]) == "key=*** again ***"


def test_formatter_redacts_messages_and_tracebacks() -> None:
    formatter = RedactingFormatter(lambda: ["sk-live-abc"])
    try:
        raise ValueError("bad key sk-live-abc")
    except ValueError as exc:
        record = logging.LogRecord(
            "t", logging.ERROR, __file__, 1, "using %s", ("sk-live-abc",), (type(exc), exc, None)
        )
    line = formatter.format(record)
    assert "sk-live-abc" not in line
    assert "***" in line


def test_log_file_lands_in_data_dir(client: TestClient, config: AppConfig) -> None:
    logging.getLogger("test").warning("hello from test")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert "hello from test" in Path(config.logs_dir / "app.log").read_text(encoding="utf-8")


def _owned_handlers() -> tuple[logging.Handler, logging.Handler]:
    handlers = [h for h in logging.getLogger().handlers if type(h).__name__.startswith("_Owned")]
    file_handler = next(h for h in handlers if type(h).__name__ == "_OwnedFileHandler")
    console = next(h for h in handlers if type(h).__name__ == "_OwnedConsoleHandler")
    return file_handler, console


def test_console_shows_only_problems_file_keeps_info(config: AppConfig) -> None:
    config.logs_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(config, lambda: ())
    file_handler, console = _owned_handlers()
    assert console.level == logging.WARNING
    assert file_handler.level == logging.NOTSET  # everything the root lets through
    assert logging.getLogger().level == logging.INFO
    for name in QUIET_LIBRARIES:
        assert logging.getLogger(name).level == logging.WARNING


def test_cli_and_debug_keep_the_console_detailed(config: AppConfig) -> None:
    config.logs_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(config, lambda: (), console_level=None)
    assert _owned_handlers()[1].level == logging.NOTSET
    setup_logging(replace(config, log_level="DEBUG"), lambda: ())
    assert _owned_handlers()[1].level == logging.NOTSET
