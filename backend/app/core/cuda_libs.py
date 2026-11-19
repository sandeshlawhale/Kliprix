"""Make NVIDIA's pip-installed CUDA libraries findable for Whisper on the GPU (D-031).

CTranslate2 (faster-whisper) loads cuBLAS (`cublas64_12.dll` / `libcublas.so.12`) at its first
GPU inference. `requirements-gpu.txt` installs it into `site-packages/nvidia/...`, a folder
the system loader does not search. On Windows the folders are added to the DLL search path;
on Linux the libraries are loaded once by full path so the later lookup by name finds them.
Nothing happens when the packages are missing (no NVIDIA GPU): Whisper then uses the CPU.
"""

from __future__ import annotations

import ctypes
import functools
import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Load order matters on Linux: cuBLAS needs cuBLASLt.
LINUX_LIBRARIES = ("libcublasLt.so.12", "libcublas.so.12")
# Keeps Windows' DLL-directory handles alive for the life of the process.
_handles: list[Any] = []


def nvidia_library_dirs() -> list[Path]:
    """`bin` (Windows) or `lib` (Linux) folders of the installed `nvidia-*` pip packages."""
    spec = importlib.util.find_spec("nvidia")
    if spec is None or not spec.submodule_search_locations:
        return []
    folder = "bin" if sys.platform == "win32" else "lib"
    found: list[Path] = []
    for root in spec.submodule_search_locations:
        found.extend(sorted(path for path in Path(root).glob(f"*/{folder}") if path.is_dir()))
    return found


@functools.cache
def register_cuda_libraries() -> list[Path]:
    """Register the NVIDIA library folders once per process; returns them (empty: none).
    A folder that cannot be added is logged and skipped; the CPU path still works."""
    folders = nvidia_library_dirs()
    for folder in folders:
        try:
            if sys.platform == "win32":
                _handles.append(os.add_dll_directory(str(folder)))
            else:
                for name in LINUX_LIBRARIES:
                    library = folder / name
                    if library.is_file():
                        _handles.append(ctypes.CDLL(str(library), mode=ctypes.RTLD_GLOBAL))
        except OSError as exc:
            logger.warning("cuda library folder not usable path=%s: %s", folder, exc)
    if folders:
        logger.info("cuda libraries registered folders=%s", len(folders))
    return folders
