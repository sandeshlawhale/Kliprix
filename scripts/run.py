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
import signal
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
    say("applying database migrations")
    run([str(python), "-m", "alembic", "upgrade", "head"], cwd=BACKEND)
    return python


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


def start(args: list[str], cwd: Path) -> subprocess.Popen[bytes]:
    if WINDOWS:
        return subprocess.Popen(  # noqa: S603 - fixed commands
            args, cwd=str(cwd), creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )
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
