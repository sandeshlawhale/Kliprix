"""Kliprix launcher, called by start.bat / start.sh (docs/06 "Scripts").

Runs the doctor, creates the backend venv if missing, installs backend and frontend
dependencies when missing or when their lockfiles changed, builds the frontend when its
sources changed, applies database migrations, starts backend and frontend, opens the
browser, and stops both on Ctrl+C.

Standard library only, and runnable on any Python 3.7+ (it may be started by a Python the
backend does not support; the backend itself uses the interpreter the doctor picks).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = BACKEND / ".venv"
WINDOWS = os.name == "nt"
BACKEND_PORT = 8000
FRONTEND_PORT = 3000
START_TIMEOUT_S = 120
POLL_S = 0.5
# Stamps record what was installed/built, so unchanged steps are skipped next time.
REQS_STAMP = VENV / "kliprix-requirements.sha256"
GPU_STAMP = VENV / "kliprix-requirements-gpu.sha256"
GPU_PROBE_TIMEOUT_S = 15
NODE_STAMP = FRONTEND / "node_modules" / ".kliprix-lock.sha256"
BUILD_STAMP = FRONTEND / ".next" / "kliprix-build.sha256"
BUILD_INPUTS = ("src", "public", "package-lock.json", "next.config.ts", "tsconfig.json")


def say(message: str) -> None:
    print(f"[kliprix] {message}", flush=True)


def fail(message: str) -> None:
    say(f"ERROR: {message}")
    sys.exit(1)


def run(args: list[str], cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    """Run a setup step; stop with its exit code when it fails."""
    say("$ " + " ".join(args))
    code = subprocess.call(args, cwd=str(cwd), env=env)  # noqa: S603 - fixed commands
    if code != 0:
        fail(f"this step failed (exit code {code}); see the output above")


def digest(paths: list[Path]) -> str:
    """SHA-256 over file names and contents (directories walked in sorted order)."""
    sha = hashlib.sha256()
    for base in paths:
        files = sorted(p for p in base.rglob("*") if p.is_file()) if base.is_dir() else [base]
        for path in files:
            if path.is_file():
                sha.update(str(path.relative_to(ROOT)).replace("\\", "/").encode())
                sha.update(path.read_bytes())
    return sha.hexdigest()


def stamp_matches(stamp: Path, value: str) -> bool:
    return stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == value


def write_stamp(stamp: Path, value: str) -> None:
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(value, encoding="utf-8")


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")


def npm() -> str:
    return "npm.cmd" if WINDOWS else "npm"


def setup_backend() -> Path:
    if not venv_python().is_file():
        picked = subprocess.run(  # noqa: S603 - fixed command
            [sys.executable, str(ROOT / "scripts" / "doctor.py"), "--print-python"],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        if picked.returncode != 0:
            fail(picked.stderr.strip() or "no supported Python (3.11-3.12) found")
        say("creating the backend virtual environment (first start only)")
        run([picked.stdout.strip(), "-m", "venv", str(VENV)])
    python = venv_python()
    requirements = BACKEND / "requirements.txt"
    wanted = digest([requirements])
    if not stamp_matches(REQS_STAMP, wanted):
        say("installing backend dependencies (first start or after an update; this takes a while)")
        run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
        run([str(python), "-m", "pip", "install", "-r", str(requirements)])
        write_stamp(REQS_STAMP, wanted)
    setup_gpu_libraries(python)
    say("applying database migrations")
    run([str(python), "-m", "alembic", "upgrade", "head"], cwd=BACKEND)
    return python


def has_nvidia_gpu() -> bool:
    """True when `nvidia-smi` lists a GPU (an NVIDIA card with its driver)."""
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return False
    try:
        listed = subprocess.run(  # noqa: S603 - fixed command
            [smi, "-L"], capture_output=True, text=True, timeout=GPU_PROBE_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return listed.returncode == 0 and "GPU" in listed.stdout


def setup_gpu_libraries(python: Path) -> None:
    """Install NVIDIA's CUDA libraries for Whisper only on PCs with an NVIDIA GPU (D-031).
    A failure is not fatal: Whisper then runs on the CPU, as on any other PC."""
    if not has_nvidia_gpu():
        return
    requirements = BACKEND / "requirements-gpu.txt"
    wanted = digest([requirements])
    if stamp_matches(GPU_STAMP, wanted):
        return
    say("NVIDIA GPU found: installing its libraries for faster Hinglish transcription (~630 MB)")
    args = [str(python), "-m", "pip", "install", "-r", str(requirements)]
    say("$ " + " ".join(args))
    code = subprocess.call(args, cwd=str(ROOT))  # noqa: S603 - fixed command
    if code != 0:
        say(f"WARNING: GPU libraries not installed (exit code {code}); Whisper will use the CPU")
        return
    write_stamp(GPU_STAMP, wanted)


def setup_frontend() -> None:
    lock = digest([FRONTEND / "package-lock.json"])
    if not stamp_matches(NODE_STAMP, lock):
        say("installing frontend dependencies (first start or after an update)")
        run([npm(), "ci"], cwd=FRONTEND)
        write_stamp(NODE_STAMP, lock)
    build = digest([FRONTEND / name for name in BUILD_INPUTS if (FRONTEND / name).exists()])
    if not stamp_matches(BUILD_STAMP, build):
        say("building the frontend (first start or after an update)")
        run([npm(), "run", "build"], cwd=FRONTEND)
        write_stamp(BUILD_STAMP, build)


def wait_for(url: str, process: subprocess.Popen[bytes], name: str) -> None:
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            fail(f"the {name} stopped while starting (exit code {process.returncode})")
        try:
            with urllib.request.urlopen(url, timeout=2):  # noqa: S310 - fixed local URL
                return
        except (urllib.error.URLError, OSError):
            time.sleep(POLL_S)
    fail(f"the {name} did not answer at {url} within {START_TIMEOUT_S} s")


# ---------- leftovers from an earlier start ----------


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _get(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 - fixed local URL
            return str(response.read(200_000).decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def is_kliprix(port: int) -> bool:
    """True when the program on `port` is a Kliprix backend or frontend."""
    if port == BACKEND_PORT:
        body = _get(f"http://127.0.0.1:{port}/openapi.json")
        return body is not None and '"title":"Kliprix"' in body.replace(" ", "")
    body = _get(f"http://127.0.0.1:{port}/")
    return body is not None and "<title>Kliprix</title>" in body


def listener_pids(port: int) -> list[int]:
    """Process ids listening on 127.0.0.1:<port> (netstat on Windows, lsof elsewhere)."""
    if WINDOWS:
        out = subprocess.run(  # noqa: S603 - fixed command
            ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, check=False
        ).stdout
        pids = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
                pids.add(int(parts[4]))
        return sorted(pids)
    try:
        out = subprocess.run(  # noqa: S603 - fixed command
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True,
            check=False,
        ).stdout  # fmt: skip
    except FileNotFoundError:
        return []
    return sorted({int(pid) for pid in out.split() if pid.isdigit()})


def kill_tree(pid: int) -> None:
    if WINDOWS:
        subprocess.call(  # noqa: S603 - fixed command
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def stop_leftovers() -> None:
    """An earlier Kliprix whose window was closed keeps the ports, and the new start would
    silently talk to the old code. Stop it (only if it really is Kliprix)."""
    for port in (BACKEND_PORT, FRONTEND_PORT):
        if not port_open(port):
            continue
        if not is_kliprix(port):
            fail(f"port {port} is used by another program; close it and start again")
        pids = listener_pids(port)
        if not pids:
            fail(f"an older Kliprix is still running on port {port}; stop it and start again")
        say(f"stopping an older Kliprix that was still running on port {port}")
        for pid in pids:
            kill_tree(pid)
        deadline = time.monotonic() + 15
        while port_open(port) and time.monotonic() < deadline:
            time.sleep(POLL_S)
        if port_open(port):
            fail(f"could not stop the older Kliprix on port {port}")


# ---------- child processes ----------


class _WindowsJob:
    """A Windows job object that kills every process in it when the launcher exits, even if
    its window is closed instead of pressing Ctrl+C. Best effort: without it, Ctrl+C still
    stops everything."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class _Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _Basic),
                ("IoInfo", ctypes.c_ulonglong * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kill_on_close = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        extended_info = 9  # JobObjectExtendedLimitInformation
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        self._handle = self._kernel32.CreateJobObjectW(None, None)
        info = _Extended()
        info.BasicLimitInformation.LimitFlags = kill_on_close
        ok = self._kernel32.SetInformationJobObject(
            self._handle, extended_info, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not self._handle or not ok:
            raise OSError("job object not available")

    def add(self, process: subprocess.Popen[bytes]) -> None:
        handle = getattr(process, "_handle", None)
        if handle is not None:
            self._kernel32.AssignProcessToJobObject(self._handle, int(handle))


_JOB: _WindowsJob | None = None


def _job() -> _WindowsJob | None:
    global _JOB
    if _JOB is None and WINDOWS:
        try:
            _JOB = _WindowsJob()
        except (OSError, AttributeError):
            say("note: closing this window may leave Kliprix running; stop it with Ctrl+C")
    return _JOB


def start(args: list[str], cwd: Path) -> subprocess.Popen[bytes]:
    if WINDOWS:
        process = subprocess.Popen(  # noqa: S603 - fixed commands
            args, cwd=str(cwd), creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )
        job = _job()
        if job is not None:
            job.add(process)
        return process
    return subprocess.Popen(args, cwd=str(cwd), start_new_session=True)  # noqa: S603


def stop(process: subprocess.Popen[bytes]) -> None:
    """Stop a process and its children (npm starts node, uvicorn may start ffmpeg)."""
    if process.poll() is not None:
        return
    if WINDOWS:
        subprocess.call(  # noqa: S603 - fixed command
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()


def main() -> None:
    parser = argparse.ArgumentParser(description="Start Kliprix (backend and frontend).")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    options = parser.parse_args()

    say("checking this computer")
    run([sys.executable, str(ROOT / "scripts" / "doctor.py")])
    python = setup_backend()
    setup_frontend()
    stop_leftovers()

    say("starting the backend on http://127.0.0.1:8000")
    backend = start(
        [str(python), "-m", "uvicorn", "app.main:create_app", "--factory",
         "--host", "127.0.0.1", "--port", str(BACKEND_PORT)],
        BACKEND,
    )  # fmt: skip
    frontend: subprocess.Popen[bytes] | None = None
    try:
        wait_for(f"http://127.0.0.1:{BACKEND_PORT}/api/health", backend, "backend")
        say("starting the frontend on http://127.0.0.1:3000")
        frontend = start([npm(), "run", "start"], FRONTEND)
        wait_for(f"http://127.0.0.1:{FRONTEND_PORT}/", frontend, "frontend")
        url = f"http://127.0.0.1:{FRONTEND_PORT}"
        say(f"Kliprix is running at {url}  (press Ctrl+C here to stop)")
        if not options.no_browser:
            webbrowser.open(url)
        while backend.poll() is None and frontend.poll() is None:
            time.sleep(POLL_S)
        say("one of the processes stopped; shutting down")
    except KeyboardInterrupt:
        say("stopping")
    finally:
        if frontend is not None:
            stop(frontend)
        stop(backend)


if __name__ == "__main__":
    main()
