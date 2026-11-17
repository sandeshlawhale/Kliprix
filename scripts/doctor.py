"""Kliprix environment doctor.

Checks that this machine can run Kliprix and prints a fix hint for every problem.
Uses only the Python standard library, because it runs before the backend venv exists.

Exit code: 0 when every required check passes (warnings allowed), 1 otherwise.
"""

from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MIN_PYTHON = (3, 11)
MIN_NODE_MAJOR = 20
MIN_RAM_GB = 4.0
LOW_FREE_RAM_GB = 1.5
MIN_FREE_DISK_GB = 5.0
SUBPROCESS_TIMEOUT_S = 15
BYTES_PER_GB = 1024**3

REPO_ROOT = Path(__file__).resolve().parent.parent

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
        result = subprocess.run(
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


def check_python() -> Check:
    version = sys.version_info
    text = f"{version.major}.{version.minor}.{version.micro} ({sys.executable})"
    if (version.major, version.minor) >= MIN_PYTHON:
        return Check("Python", OK, text)
    return Check(
        "Python",
        FAIL,
        text,
        f"Install Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer from https://www.python.org/downloads/",
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
    if major >= MIN_NODE_MAJOR:
        return Check("Node.js", OK, detail)
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
    configured = os.environ.get("DATA_DIR", "").strip()
    return Path(configured).resolve() if configured else REPO_ROOT / "data"


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
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
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


def main() -> int:
    checks = run_checks()
    print("Kliprix doctor")
    print(f"System: {platform.system()} {platform.release()} ({platform.machine()})")
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
