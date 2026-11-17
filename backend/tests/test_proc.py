import sys
import threading
import time

import pytest

from app.core.proc import (
    CommandCancelledError,
    CommandNotFoundError,
    CommandTimeoutError,
    run_command,
)

SLEEP = [sys.executable, "-c", "import time; time.sleep(30)"]


def test_returns_output_and_exit_code() -> None:
    code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
    result = run_command([sys.executable, "-c", code], timeout_s=30)
    assert (result.returncode, result.stdout.strip(), result.stderr.strip()) == (3, "out", "err")


def test_missing_program() -> None:
    with pytest.raises(CommandNotFoundError):
        run_command(["definitely-not-a-real-program-kliprix"], timeout_s=5)


def test_timeout_kills_process() -> None:
    started = time.monotonic()
    with pytest.raises(CommandTimeoutError):
        run_command(SLEEP, timeout_s=0.5)
    assert time.monotonic() - started < 10


def test_cancel_kills_process() -> None:
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(CommandCancelledError):
        run_command(SLEEP, timeout_s=30, cancel=cancel)
    assert time.monotonic() - started < 10
