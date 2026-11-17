"""The one helper for running external programs (ffmpeg, ffprobe, nvidia-smi, yt-dlp).

Always an argument list (never a shell), always a timeout, stderr captured, and the
process is killed on timeout or when the caller's cancel event is set.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass

# How often a running command checks its cancel event.
CANCEL_POLL_S = 0.2


class CommandError(Exception):
    """Base class for failures to run a command to completion."""


class CommandNotFoundError(CommandError):
    """The program does not exist or cannot be started."""


class CommandTimeoutError(CommandError):
    """The program ran longer than its timeout and was killed."""


class CommandCancelledError(CommandError):
    """The cancel event was set and the program was killed."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def run_command(
    args: Sequence[str],
    timeout_s: float,
    cancel: threading.Event | None = None,
) -> CommandResult:
    """Run `args` to completion and return its exit code and decoded output.

    Raises:
        CommandNotFoundError: the executable cannot be started.
        CommandTimeoutError: it exceeded `timeout_s` (the process is killed).
        CommandCancelledError: `cancel` was set (the process is killed).
    """
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    try:
        # Arg list, no shell: callers pass fixed program paths and validated values.
        process = subprocess.Popen(  # noqa: S603
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise CommandNotFoundError(f"cannot start {args[0]!r}: {exc.strerror}") from exc
    deadline = time.monotonic() + timeout_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _kill(process)
            raise CommandTimeoutError(f"{args[0]!r} timed out after {timeout_s:.0f} s")
        if cancel is not None and cancel.is_set():
            _kill(process)
            raise CommandCancelledError(f"{args[0]!r} was cancelled")
        try:
            stdout, stderr = process.communicate(timeout=min(CANCEL_POLL_S, remaining))
        except subprocess.TimeoutExpired:
            continue
        return CommandResult(process.returncode, stdout, stderr)


def _kill(process: subprocess.Popen[str]) -> None:
    process.kill()
    # Reap the process and close its pipes so nothing leaks.
    process.communicate()
