"""The one helper for running external programs (ffmpeg, ffprobe, nvidia-smi, yt-dlp).

Always an argument list (never a shell), always a timeout, stderr captured, and the
process is killed on timeout or when the caller's cancel event is set.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)

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


class CommandFailedError(CommandError):
    """The program exited with a non-zero code; `stderr_tail` holds its last output lines."""

    def __init__(self, message: str, stderr_tail: str) -> None:
        super().__init__(message)
        self.stderr_tail = stderr_tail


# Lines of stderr kept for logging when a streamed command fails.
STDERR_TAIL_LINES = 20


class StreamingCommand:
    """Run a program whose stdout is read piece by piece (frames, PCM) while it runs.

    A watchdog thread kills the process when the timeout passes or `cancel` is set, so a
    blocked read always returns. stderr goes to a temp file to avoid pipe deadlocks.
    Use as a context manager; `close()` waits for the exit and raises on failure.
    Optionally `stdin_pipe=True` gives a writable `stdin` for feeding frames.
    """

    def __init__(
        self,
        args: Sequence[str],
        timeout_s: float,
        cancel: threading.Event | None = None,
        *,
        stdin_pipe: bool = False,
        stdout_pipe: bool = True,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self._name = args[0]
        self._timeout_s = timeout_s
        # Closed in _shutdown; the object outlives this method, so no `with` block.
        self._stderr = tempfile.TemporaryFile()  # noqa: SIM115
        try:
            # Arg list, no shell: callers pass fixed program paths and validated values.
            self.process = subprocess.Popen(  # noqa: S603
                list(args),
                stdout=subprocess.PIPE if stdout_pipe else subprocess.DEVNULL,
                stderr=self._stderr,
                stdin=subprocess.PIPE if stdin_pipe else subprocess.DEVNULL,
            )
        except OSError as exc:
            self._stderr.close()
            raise CommandNotFoundError(f"cannot start {args[0]!r}: {exc.strerror}") from exc
        self._reason: str | None = None
        self._done = threading.Event()
        self._cancel = cancel
        self._deadline = time.monotonic() + timeout_s
        self._watchdog = threading.Thread(target=self._watch, daemon=True)
        self._watchdog.start()

    def _watch(self) -> None:
        while not self._done.wait(CANCEL_POLL_S):
            if self.process.poll() is not None:
                return
            if self._cancel is not None and self._cancel.is_set():
                self._reason = "cancelled"
            elif time.monotonic() > self._deadline:
                self._reason = "timeout"
            if self._reason is not None:
                self.process.kill()
                return

    def read_exact(self, size: int) -> bytes | None:
        """Read exactly `size` bytes from stdout; None at the end of the stream."""
        assert self.process.stdout is not None  # noqa: S101 - set when stdout_pipe=True
        data = bytearray()
        while len(data) < size:
            chunk = self.process.stdout.read(size - len(data))
            if not chunk:
                break
            data.extend(chunk)
        self._raise_if_stopped()
        if not data:
            return None
        return bytes(data)

    def write(self, data: bytes) -> None:
        """Write to stdin (only with `stdin_pipe=True`)."""
        assert self.process.stdin is not None  # noqa: S101 - set when stdin_pipe=True
        try:
            self.process.stdin.write(data)
        except (BrokenPipeError, OSError) as exc:
            self._raise_if_stopped()
            raise CommandFailedError(
                f"{self._name!r} stopped reading its input", self._stderr_tail()
            ) from exc

    def close_stdin(self) -> None:
        """Signal end of input (only with `stdin_pipe=True`)."""
        if self.process.stdin is not None and not self.process.stdin.closed:
            try:
                self.process.stdin.close()
            except OSError:
                logger.debug("stdin of %s already closed", self._name)

    def _raise_if_stopped(self) -> None:
        if self._reason == "cancelled":
            raise CommandCancelledError(f"{self._name!r} was cancelled")
        if self._reason == "timeout":
            raise CommandTimeoutError(f"{self._name!r} timed out after {self._timeout_s:.0f} s")

    def _stderr_tail(self) -> str:
        self._stderr.seek(0)
        text = self._stderr.read().decode("utf-8", errors="replace")
        return "\n".join(text.splitlines()[-STDERR_TAIL_LINES:])

    def close(self) -> None:
        """Close stdin, wait for the exit and raise if it failed, was cancelled or timed out."""
        try:
            self.close_stdin()
            remaining = max(self._deadline - time.monotonic(), CANCEL_POLL_S)
            try:
                code = self.process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                self._reason = "timeout"
                self.process.kill()
                self.process.wait()
                code = -1
            self._raise_if_stopped()
            if code != 0:
                raise CommandFailedError(
                    f"{self._name!r} exited with code {code}", self._stderr_tail()
                )
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        self._done.set()
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        if self.process.stdout is not None:
            self.process.stdout.close()
        self._stderr.close()

    def __enter__(self) -> StreamingCommand:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            self.close()
        else:
            self._shutdown()
