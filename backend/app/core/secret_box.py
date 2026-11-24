"""Encryption for the saved LLM key (D-053).

The key is encrypted with Fernet (AES-128-CBC + HMAC-SHA256, `cryptography`) before it is
written to the database. The Fernet key lives in `DATA_DIR/secret.key`, separate from the
database. On Windows that file is additionally wrapped with DPAPI (`CryptProtectData`), so
only the same Windows user on the same PC can unwrap it; elsewhere it is a 0600 file.
A copied database alone never reveals the API key.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

SECRET_FILE_NAME = "secret.key"  # noqa: S105 (a file name, not a secret)
# File prefix that marks a DPAPI-wrapped key (Windows) versus a raw Fernet key.
_DPAPI_MAGIC = b"KLPX-DPAPI1:"


class SecretBoxError(Exception):
    """The saved secret cannot be decrypted (key file changed, missing, or corrupt)."""


class SecretBox:
    """Encrypts and decrypts short secrets with a per-install key."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / SECRET_FILE_NAME
        self._fernet: Fernet | None = None

    def encrypt(self, plain: str) -> str:
        return self._get(create=True).encrypt(plain.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        """Raises SecretBoxError if the token cannot be read with this install's key."""
        try:
            return self._get(create=False).decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, OSError) as exc:
            raise SecretBoxError("The saved secret cannot be decrypted.") from exc

    def _get(self, *, create: bool) -> Fernet:
        if self._fernet is None:
            if self._path.exists():
                key = _unwrap(self._path.read_bytes())
            elif create:
                key = Fernet.generate_key()
                _write_private(self._path, _wrap(key))
            else:
                raise SecretBoxError("No encryption key file.")
            self._fernet = Fernet(key)
        return self._fernet


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def _wrap(key: bytes) -> bytes:
    if sys.platform == "win32":
        return _DPAPI_MAGIC + _dpapi(key, protect=True)
    return key


def _unwrap(data: bytes) -> bytes:
    if data.startswith(_DPAPI_MAGIC):
        if sys.platform != "win32":
            raise SecretBoxError("The key file was made on Windows.")
        try:
            return _dpapi(data[len(_DPAPI_MAGIC) :], protect=False)
        except OSError as exc:
            raise SecretBoxError("The key file belongs to another Windows user.") from exc
    return data.strip()


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    """CryptProtectData / CryptUnprotectData for the current Windows user."""
    windll = getattr(ctypes, "windll")  # noqa: B009 (Windows-only attribute, typed on all OSes)
    crypt32 = windll.crypt32
    kernel32 = windll.kernel32
    buffer = ctypes.create_string_buffer(data, len(data))
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = _DataBlob()
    ui_forbidden = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    call = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = call(ctypes.byref(source), None, None, None, None, ui_forbidden, ctypes.byref(result))
    if not ok:
        raise OSError("DPAPI call failed.")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)
