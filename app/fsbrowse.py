"""Folder picker for Settings: lists subfolders, but only inside the folders mounted into the container.

The mounted folders (the TrueNAS host paths / volumes) are read from /proc/self/mountinfo, so the picker shows
what the app can actually use and never the container's own system folders. Hidden folders (MediaMan's
.mediaman-work / .mediaman-trash, .snapshots, ...) are left out.
"""
from __future__ import annotations

import os
import re

from . import config

MOUNTINFO = "/proc/self/mountinfo"
SYSTEM = ("/proc", "/sys", "/dev", "/etc", "/run", "/usr", "/opt", "/var", "/tmp", "/bin", "/sbin", "/lib", "/boot")
MAX_ENTRIES = 2000


def _unescape(s: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def roots(mountinfo: str = MOUNTINFO) -> list[str]:
    """Top-level mounted folders, excluding system mounts and MediaMan's own config folder."""
    found = []
    try:
        with open(mountinfo) as f:
            for line in f:
                parts = line.split()
                if len(parts) > 4:
                    found.append(_unescape(parts[4]))
    except OSError:
        pass
    cfg = os.path.realpath(config.CONFIG_DIR)
    keep = sorted({p for p in found if p != "/" and p != cfg and not p.startswith(SYSTEM + (cfg + "/",))
                   and os.path.isdir(p)})
    top = [p for p in keep if not any(p.startswith(q.rstrip("/") + "/") for q in keep if q != p)]
    return top or ["/"]


def _inside(path: str, rts: list[str]) -> bool:
    return any(r == "/" or path == r or path.startswith(r.rstrip("/") + "/") for r in rts)


def listing(path: str | None) -> dict:
    """Subfolders of `path` (default: the mounted folders). Raises ValueError for paths outside them."""
    rts = roots()
    if not path:
        return {"path": None, "parent": None, "roots": rts,
                "dirs": [{"name": r, "path": r} for r in rts], "writable": False}
    real = os.path.realpath(path)
    if not _inside(real, rts):
        raise ValueError("only folders mounted into MediaMan can be browsed")
    if not os.path.isdir(real):
        raise ValueError("folder not found")
    try:
        names = sorted((e.name for e in os.scandir(real) if e.is_dir(follow_symlinks=True)
                        and not e.name.startswith(".")), key=str.casefold)
    except PermissionError:
        raise ValueError("MediaMan isn't allowed to read this folder")
    parent = os.path.dirname(real) if real not in rts else None
    if parent is not None and not _inside(parent, rts):
        parent = None
    return {"path": real, "parent": parent, "roots": rts,
            "dirs": [{"name": n, "path": os.path.join(real, n)} for n in names[:MAX_ENTRIES]],
            "truncated": len(names) > MAX_ENTRIES, "writable": os.access(real, os.W_OK)}
