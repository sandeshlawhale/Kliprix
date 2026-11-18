"""Kliprix environment doctor.

Checks that this machine can run Kliprix and prints a fix hint for every problem.
Uses only the Python standard library, because it runs before the backend venv exists.

Exit code: 0 when every required check passes (warnings allowed), 1 otherwise.

`python scripts/doctor.py --print-python` prints only the path of the Python that the
backend must use (a supported version, preferring the existing backend venv) and exits 0,
or prints the reason to stderr and exits 1. Start scripts use this to pick the interpreter.

This file must stay runnable on any Python 3 (it may be started by an unsupported one),
so it avoids syntax newer than 3.7.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Backend Python range (inclusive). The upper bound exists because later dependencies
# (faster-whisper/ctranslate2, MediaPipe) ship wheels for these versions first (AA-002, AA-009).
SUPPORTED_PYTHON = ((3, 11), (3, 12))
MIN_NODE_MAJOR = 20
# yt-dlp solves YouTube's player challenges with Node only from this major version (AA-017).
YTDLP_MIN_NODE_MAJOR = 22
MIN_RAM_GB = 4.0
LOW_FREE_RAM_GB = 1.5
MIN_FREE_DISK_GB = 5.0
SUBPROCESS_TIMEOUT_S = 15
BYTES_PER_GB = 1024**3

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_VENV = REPO_ROOT / "backend" / ".venv"
_VERSION_PROBE = "import sys; v = sys.version_info; print(v[0], v[1], v[2]); print(sys.executable)"

OK, WARN, FAIL = "OK", "WARN", "FAIL"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    hint: str = ""


def _run(args: list[str]) -> str | None:
    """Run a command and return its stdout, or None if it cannot run."""
    try:
        # Arg list, no shell; commands come from PATH lookups and fixed names only.
        result = subprocess.run(  # noqa: S603
            args,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _ffmpeg_install_hint() -> str:
    system = platform.system()
    if system == "Windows":
        return "Install FFmpeg: `winget install Gyan.FFmpeg`, then open a new terminal."
    if system == "Darwin":
        return "Install FFmpeg: `brew install ffmpeg`."
    return "Install FFmpeg with your package manager, e.g. `sudo apt install ffmpeg`."


def _supported_text() -> str:
    low, high = SUPPORTED_PYTHON
    return f"{low[0]}.{low[1]} to {high[0]}.{high[1]}"


def _newest_supported() -> str:
    major, minor = SUPPORTED_PYTHON[1]
    return f"{major}.{minor}"


def _is_supported(version: tuple[int, int]) -> bool:
    return SUPPORTED_PYTHON[0] <= version <= SUPPORTED_PYTHON[1]


@dataclass
class Interpreter:
    path: str
    version: tuple[int, int, int]

    @property
    def label(self) -> str:
        major, minor, micro = self.version
        return f"{major}.{minor}.{micro} ({self.path})"


def _probe(command: list[str]) -> Interpreter | None:
    """Ask a Python command for its version and real executable path."""
    output = _run([*command, "-c", _VERSION_PROBE])
    if output is None:
        return None
    lines = output.strip().splitlines()
    if len(lines) != 2:
        return None
    try:
        major, minor, micro = (int(part) for part in lines[0].split())
    except ValueError:
        return None
    return Interpreter(lines[1].strip(), (major, minor, micro))


def _venv_python() -> Path:
    if platform.system() == "Windows":
        return BACKEND_VENV / "Scripts" / "python.exe"
    return BACKEND_VENV / "bin" / "python"


def _candidate_commands() -> list[list[str]]:
    """Python commands to try, newest supported version first."""
    (low_major, low_minor), (high_major, high_minor) = SUPPORTED_PYTHON
    assert low_major == high_major == 3  # noqa: S101 - the range never spans majors
    high_to_low = [(3, minor) for minor in range(high_minor, low_minor - 1, -1)]
    commands: list[list[str]] = []
    launcher = shutil.which("py")
    if launcher is not None:
        commands += [[launcher, f"-{major}.{minor}"] for major, minor in high_to_low]
    for major, minor in high_to_low:
        found = shutil.which(f"python{major}.{minor}")
        if found is not None:
            commands.append([found])
    commands.append([sys.executable])
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found is not None:
            commands.append([found])
    return commands


def find_system_python() -> tuple[Interpreter | None, list[Interpreter]]:
    """Return (first supported interpreter, every interpreter seen) outside the venv."""
    seen: list[Interpreter] = []
    for command in _candidate_commands():
        interpreter = _probe(command)
        if interpreter is None:
            continue
        if all(os.path.normcase(o.path) != os.path.normcase(interpreter.path) for o in seen):
            seen.append(interpreter)
        if _is_supported(interpreter.version[:2]):
            return interpreter, seen
    return None, seen


def find_venv_python() -> Interpreter | None:
    """Return the backend venv's interpreter if the venv exists and runs."""
    path = _venv_python()
    if not path.is_file():
        return None
    return _probe([str(path)])


def _no_python_hint(seen: list[Interpreter]) -> str:
    found = ", ".join(i.label for i in seen) if seen else "none"
    return (
        f"Kliprix needs Python {_supported_text()} (found: {found}). "
        f"Install Python {_newest_supported()} from https://www.python.org/downloads/ "
        "(newer versions are not supported yet); it can live next to your current Python."
    )


def pick_backend_python() -> tuple[Interpreter | None, str]:
    """Choose the interpreter the backend must use.

    Returns (interpreter, "") on success, or (None, reason) when no usable one exists.
    The backend venv wins when its version is supported; a venv with an unsupported
    version is an error, because silently using another Python would hide it.
    """
    venv = find_venv_python()
    if venv is not None:
        if _is_supported(venv.version[:2]):
            return venv, ""
        system, _ = find_system_python()
        create = (
            f'"{system.path}" -m venv'
            if system is not None
            else f"Python {_newest_supported()} -m venv"
        )
        return None, (
            f"The backend venv uses Python {venv.label}, which is not supported "
            f"(needs {_supported_text()}). Delete backend/.venv and recreate it with "
            f"{create} backend/.venv"
        )
    system, seen = find_system_python()
    if system is None:
        return None, _no_python_hint(seen)
    return system, ""


def check_python() -> Check:
    venv = find_venv_python()
    if venv is not None:
        chosen, reason = pick_backend_python()
        if chosen is None:
            return Check("Python", FAIL, f"backend venv: {venv.label}", reason)
        return Check("Python", OK, f"backend venv: {chosen.label}")
    system, seen = find_system_python()
    if system is None:
        found = ", ".join(i.label for i in seen) if seen else "no Python found"
        return Check("Python", FAIL, f"no supported Python ({found})", _no_python_hint(seen))
    return Check(
        "Python",
        OK,
        f"{system.label}; backend venv not created yet",
        f'Create it: "{system.path}" -m venv backend/.venv',
    )


def check_node() -> Check:
    node = shutil.which("node")
    if node is None:
        return Check(
            "Node.js",
            FAIL,
            "not found on PATH",
            f"Install Node.js {MIN_NODE_MAJOR} LTS or newer from https://nodejs.org/",
        )
    output = _run([node, "--version"])
    match = re.match(r"v(\d+)\.", output or "")
    if match is None:
        return Check("Node.js", FAIL, f"could not read version from {node}", "Reinstall Node.js.")
    major = int(match.group(1))
    detail = f"{(output or '').strip()} ({node})"
    if major >= YTDLP_MIN_NODE_MAJOR:
        return Check("Node.js", OK, detail)
    if major >= MIN_NODE_MAJOR:
        return Check(
            "Node.js",
            WARN,
            detail,
            f"YouTube downloads need Node.js {YTDLP_MIN_NODE_MAJOR} or newer (yt-dlp); "
            "upgrade from https://nodejs.org/",
        )
    return Check(
        "Node.js",
        FAIL,
        detail,
        f"Upgrade to Node.js {MIN_NODE_MAJOR} LTS or newer from https://nodejs.org/",
    )


def _find_ffmpeg_tool(name: str) -> str | None:
    """Locate ffmpeg/ffprobe, preferring the folder of FFMPEG_PATH when set."""
    configured = os.environ.get("FFMPEG_PATH", "").strip()
    if configured:
        folder = Path(configured).parent
        for candidate in (folder / name, folder / f"{name}.exe"):
            if candidate.is_file():
                return str(candidate)
    return shutil.which(name)


def check_ffmpeg_tool(name: str) -> Check:
    path = _find_ffmpeg_tool(name)
    if path is None:
        return Check(name, FAIL, "not found on PATH", _ffmpeg_install_hint())
    output = _run([path, "-hide_banner", "-version"])
    if output is None:
        return Check(name, FAIL, f"found at {path} but it does not run", _ffmpeg_install_hint())
    first_line = output.splitlines()[0].split(" Copyright")[0] if output else "unknown version"
    return Check(name, OK, f"{first_line} ({path})")


def check_libass() -> Check:
    path = _find_ffmpeg_tool("ffmpeg")
    if path is None:
        return Check("libass", FAIL, "ffmpeg not found", _ffmpeg_install_hint())
    output = _run([path, "-hide_banner", "-filters"])
    if output is None:
        return Check("libass", FAIL, "could not list ffmpeg filters", _ffmpeg_install_hint())
    # The `ass` and `subtitles` filters only exist when ffmpeg is built with libass.
    filters = {line.split()[1] for line in output.splitlines() if len(line.split()) > 1}
    if {"ass", "subtitles"} <= filters:
        return Check("libass", OK, "ffmpeg has the ass and subtitles filters")
    return Check(
        "libass",
        FAIL,
        "ffmpeg was built without libass (captions cannot be rendered)",
        "Install a full FFmpeg build that includes libass. " + _ffmpeg_install_hint(),
    )


def _data_dir() -> Path:
    # Same rule as the backend (AA-010): relative paths are relative to the repo root.
    configured = os.environ.get("DATA_DIR", "").strip()
    if not configured:
        return REPO_ROOT / "data"
    path = Path(configured).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def check_disk() -> Check:
    target = _data_dir()
    probe = target
    # The data folder may not exist yet; measure the drive it will live on.
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free_gb = shutil.disk_usage(probe).free / BYTES_PER_GB
    detail = f"{free_gb:.1f} GB free for {target}"
    if free_gb >= MIN_FREE_DISK_GB:
        return Check("Disk space", OK, detail)
    return Check(
        "Disk space",
        WARN,
        detail,
        f"Free at least {MIN_FREE_DISK_GB:.0f} GB; downloads and renders need room.",
    )


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _memory_bytes() -> tuple[int, int] | None:
    """Return (total, available) physical memory in bytes, or None if unknown."""
    system = platform.system()
    if system == "Windows":
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined,unused-ignore]
            return status.ullTotalPhys, status.ullAvailPhys
        return None
    if system == "Linux":
        values: dict[str, int] = {}
        try:
            with open("/proc/meminfo", encoding="ascii") as handle:
                for line in handle:
                    key, _, rest = line.partition(":")
                    values[key] = int(rest.split()[0]) * 1024
        except (OSError, ValueError, IndexError):
            return None
        if "MemTotal" in values and "MemAvailable" in values:
            return values["MemTotal"], values["MemAvailable"]
        return None
    if system == "Darwin":
        total = _run(["sysctl", "-n", "hw.memsize"])
        vm = _run(["vm_stat"])
        if total is None or vm is None:
            return None
        page_size_match = re.search(r"page size of (\d+) bytes", vm)
        page_size = int(page_size_match.group(1)) if page_size_match else 4096
        free_pages = 0
        for label in ("Pages free", "Pages inactive", "Pages speculative"):
            match = re.search(rf"{label}:\s+(\d+)", vm)
            if match:
                free_pages += int(match.group(1))
        return int(total.strip()), free_pages * page_size
    return None


def check_ram() -> Check:
    memory = _memory_bytes()
    if memory is None:
        return Check("RAM", WARN, "could not detect memory on this system")
    total_gb, available_gb = (value / BYTES_PER_GB for value in memory)
    detail = f"{total_gb:.1f} GB total, {available_gb:.1f} GB available"
    # Small tolerance: a "4 GB" machine reports slightly less usable memory.
    if total_gb < MIN_RAM_GB * 0.9:
        return Check(
            "RAM",
            WARN,
            detail,
            f"Below the {MIN_RAM_GB:.0f} GB minimum; processing may fail. Close other apps.",
        )
    if available_gb < LOW_FREE_RAM_GB:
        return Check(
            "RAM",
            WARN,
            detail,
            "Little free memory right now; close other apps before processing.",
        )
    return Check("RAM", OK, detail)


def run_checks() -> list[Check]:
    return [
        check_python(),
        check_node(),
        check_ffmpeg_tool("ffmpeg"),
        check_ffmpeg_tool("ffprobe"),
        check_libass(),
        check_disk(),
        check_ram(),
    ]


def print_python() -> int:
    """Print the backend interpreter path for start scripts; reason on stderr if none."""
    chosen, reason = pick_backend_python()
    if chosen is None:
        print(reason, file=sys.stderr)
        return 1
    print(chosen.path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that this machine can run Kliprix.")
    parser.add_argument(
        "--print-python",
        action="store_true",
        help="print only the Python the backend must use, then exit",
    )
    if parser.parse_args().print_python:
        return print_python()
    checks = run_checks()
    print("Kliprix doctor")
    print(f"System: {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Doctor runs on: Python {platform.python_version()} ({sys.executable})")
    print()
    for check in checks:
        print(f"[{check.status:<4}] {check.name:<11} {check.detail}")
        if check.hint:
            print(f"       fix: {check.hint}")
    failures = sum(check.status == FAIL for check in checks)
    warnings = sum(check.status == WARN for check in checks)
    print()
    if failures:
        print(f"{failures} problem(s) must be fixed before Kliprix can run.")
        return 1
    print(f"Ready. {warnings} warning(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
