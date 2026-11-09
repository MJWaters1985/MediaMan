"""Optional HTTPS: serve with a PEM cert/key pair and restart onto renewed files.

Set MEDIAMAN_TLS_CERT and MEDIAMAN_TLS_KEY (paths inside the container). The cert file
should contain the full chain. When the files change (e.g. a renewal tool replaced them),
MediaMan waits until no transcode is running, checks the new pair loads, and restarts
itself; the container restart policy brings it straight back on the new certificate.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import signal
import ssl

CERT = os.environ.get("MEDIAMAN_TLS_CERT", "").strip()
KEY = os.environ.get("MEDIAMAN_TLS_KEY", "").strip()

state: dict = {"requested": bool(CERT or KEY), "enabled": False, "error": None, "cert": None}


def _sig(*paths: str):
    out = []
    for p in paths:
        st = os.stat(p)
        out.append((st.st_size, int(st.st_mtime)))
    return tuple(out)


def check_pair(cert: str = CERT, key: str = KEY) -> str | None:
    """Return None if the pair loads, else a reason."""
    if not cert or not key:
        return "set both MEDIAMAN_TLS_CERT and MEDIAMAN_TLS_KEY"
    for p in (cert, key):
        if not os.path.exists(p):
            return f"{p} not found"
        if not os.access(p, os.R_OK):
            return f"{p} not readable by uid {os.getuid()}"
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
    except (ssl.SSLError, OSError) as e:
        return f"cert/key don't load: {e}"
    return None


def cert_info(path: str = CERT) -> dict | None:
    try:
        d = ssl._ssl._test_decode_cert(path)  # stdlib helper; fine for display
    except Exception:
        return None
    subj = dict(x[0] for x in d.get("subject", ()))
    iss = dict(x[0] for x in d.get("issuer", ()))
    with open(path) as f:
        chain = f.read().count("BEGIN CERTIFICATE")
    exp = dt.datetime.strptime(d["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt.timezone.utc)
    return {
        "subject": subj.get("commonName"),
        "names": [v for k, v in d.get("subjectAltName", ()) if k == "DNS"],
        "issuer": iss.get("commonName") or iss.get("organizationName"),
        "not_after": exp.isoformat(),
        "days_left": (exp - dt.datetime.now(dt.timezone.utc)).days,
        "chain_length": chain,
    }


def resolve() -> dict:
    """Decide at boot whether to serve HTTPS. Never crash-loops: falls back to HTTP with an error."""
    if not state["requested"]:
        return state
    err = check_pair()
    if err:
        state["error"] = err
        print(f"[warn] tls: HTTPS requested but {err} — serving plain HTTP", flush=True)
        return state
    state["enabled"] = True
    state["cert"] = cert_info()
    state["sig"] = _sig(CERT, KEY)
    return state


async def watch_for_renewal(is_busy, log, interval: int = 300):
    """Restart onto renewed cert files once idle."""
    if not state["enabled"]:
        return
    while True:
        await asyncio.sleep(interval)
        try:
            sig = _sig(CERT, KEY)
        except OSError:
            continue
        if sig == state["sig"]:
            continue
        err = check_pair()
        if err:            # mid-write or mismatched pair: try again next round
            continue
        if is_busy():
            continue
        info = cert_info() or {}
        log("info", "tls", f"certificate renewed (expires {info.get('not_after', '?')[:10]}) — restarting to load it")
        os.kill(os.getpid(), signal.SIGTERM)
        return
