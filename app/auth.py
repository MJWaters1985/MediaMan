"""Web UI password (HTTP basic auth; the user name is ignored).

Set from Settings and stored as a salted scrypt hash in auth.json in the config directory, never in plain text.
Forgotten password: delete auth.json from the config folder and restart the app.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading

from . import config

MIN_LENGTH = 8
_N, _R, _P = 2 ** 14, 8, 1
_lock = threading.Lock()
_ok_headers: set[str] = set()     # Authorization headers already verified (scrypt is too slow to run per request)


def _file() -> str:
    return os.path.join(config.CONFIG_DIR, "auth.json")


def _hash(pw: str, salt: bytes) -> str:
    return hashlib.scrypt(pw.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32, maxmem=64 * 1024 * 1024).hex()


def _stored() -> dict | None:
    try:
        with open(_file()) as f:
            d = json.load(f)
        return d if d.get("hash") and d.get("salt") else None
    except (OSError, ValueError):
        return None


def state() -> dict:
    return {"enabled": _stored() is not None}


def check(pw: str) -> bool:
    d = _stored()
    if not d:
        return True
    return hmac.compare_digest(_hash(pw, bytes.fromhex(d["salt"])), d["hash"])


def authorized(header: str) -> bool:
    """True when no password is set, or the Basic Authorization header carries the right one."""
    if not state()["enabled"]:
        return True
    if header in _ok_headers:
        return True
    if not header.lower().startswith("basic "):
        return False
    try:
        _, _, pw = base64.b64decode(header[6:]).decode().partition(":")
    except Exception:
        return False
    if not check(pw):
        return False
    with _lock:
        _ok_headers.add(header)
    return True


def set_password(current: str, new: str) -> None:
    """Set, change or (new == "") remove the password. Raises ValueError with a message for the UI."""
    if _stored() and not check(current or ""):
        raise ValueError("Current password is wrong")
    if new and len(new) < MIN_LENGTH:
        raise ValueError(f"Use at least {MIN_LENGTH} characters")
    with _lock:
        _ok_headers.clear()
        if not new:
            try:
                os.unlink(_file())
            except FileNotFoundError:
                pass
            return
        salt = secrets.token_bytes(16)
        os.makedirs(config.CONFIG_DIR, exist_ok=True)
        tmp = _file() + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"v": 1, "algo": f"scrypt-{_N}-{_R}-{_P}", "salt": salt.hex(), "hash": _hash(new, salt)}, f)
        os.replace(tmp, _file())
