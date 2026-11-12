"""Certificate checks for servers that leave out their intermediate certificates.

A server should send its own certificate plus the intermediates that link it to a trusted root. Some don't (Jellyfin
serving a .pfx often sends only its own certificate). Browsers cope by downloading the missing certificates from the
"CA Issuers" address written in each certificate (its Authority Information Access extension); Python doesn't, so the
check fails with "unable to get local issuer certificate". MediaMan does what browsers do: it follows those addresses,
up to a few levels, until the chain reaches a root it already trusts, and keeps what it fetched in /config.

The checks stay strict: fetched certificates only help build a chain, which must still end at a trusted root
(partial chains are not accepted), and the server's name is still checked.
"""
from __future__ import annotations

import os
import socket
import ssl
import threading
from urllib.parse import urlsplit

import certifi
import httpx

from . import config, db

# DER for the OID id-ad-caIssuers (1.3.6.1.5.5.7.48.2); the URI follows as a [6] IA5String
_CA_ISSUERS = bytes.fromhex("06082b06010505073002")
MAX_LEVELS = 4
MAX_CERT_BYTES = 64 * 1024
_lock = threading.Lock()
_ctx: dict = {"ctx": None, "mtime": None}


def cache_file() -> str:
    return os.path.join(config.CONFIG_DIR, "intermediate-certs.pem")


def _new_context(extra_pem: str = "") -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)   # a fetched intermediate is never a root
    if extra_pem:
        ctx.load_verify_locations(cadata=extra_pem)
    return ctx


def _cached_pem() -> str:
    try:
        with open(cache_file()) as f:
            return f.read()
    except OSError:
        return ""


def context() -> ssl.SSLContext:
    """The trusted roots plus any intermediates fetched earlier."""
    with _lock:
        try:
            mtime = os.path.getmtime(cache_file())
        except OSError:
            mtime = None
        if _ctx["ctx"] is None or _ctx["mtime"] != mtime:
            _ctx.update(ctx=_new_context(_cached_pem()), mtime=mtime)
        return _ctx["ctx"]


def verify(enabled) -> ssl.SSLContext | bool:
    """What to pass as httpx's verify= for a connection whose certificate check is `enabled`."""
    return context() if enabled else False


def missing_issuer(e: BaseException) -> bool:
    s = str(e)
    return "unable to get local issuer certificate" in s or "unable to get issuer certificate" in s


def ca_issuers_url(der: bytes) -> str | None:
    """The http(s) "CA Issuers" address in a DER certificate, if it has one."""
    i = der.find(_CA_ISSUERS)
    if i < 0:
        return None
    j = i + len(_CA_ISSUERS)
    if j + 2 > len(der) or der[j] != 0x86 or der[j + 1] >= 0x80:     # [6] IA5String, short length form
        return None
    url = der[j + 2:j + 2 + der[j + 1]].decode("ascii", "replace")
    return url if url.startswith(("http://", "https://")) else None


def _download(url: str) -> bytes:
    with httpx.stream("GET", url, timeout=10, follow_redirects=True) as r:
        r.raise_for_status()
        data = b""
        for chunk in r.iter_bytes():
            data += chunk
            if len(data) > MAX_CERT_BYTES:
                raise ValueError("too large to be a certificate")
    return data


def _to_der(data: bytes) -> bytes:
    if data.lstrip().startswith(b"-----BEGIN"):
        return ssl.PEM_cert_to_DER_cert(data.decode("ascii", "replace"))
    return data


def _handshake_ok(host: str, port: int, ctx: ssl.SSLContext) -> bool:
    try:
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                return True
    except ssl.SSLCertVerificationError:
        return False


def complete_chain(url: str) -> bool:
    """Fetch the intermediates `url`'s server leaves out, following each certificate's CA Issuers address until
    the chain checks out. Keeps them for later and returns True if the server's certificate now verifies."""
    parts = urlsplit(url)
    host, port = parts.hostname, parts.port or 443
    if not host:
        return False
    try:
        der = ssl.PEM_cert_to_DER_cert(ssl.get_server_certificate((host, port), timeout=10))
        cached, fetched = _cached_pem(), []
        for _ in range(MAX_LEVELS):
            issuer = ca_issuers_url(der)
            if not issuer:
                break
            der = _to_der(_download(issuer))
            fetched.append(ssl.DER_cert_to_PEM_cert(der))
            if _handshake_ok(host, port, _new_context(cached + "".join(fetched))):
                new = [p for p in fetched if p not in cached]
                with _lock:
                    with open(cache_file(), "a") as f:
                        f.write("".join(new))
                    _ctx["ctx"] = None
                db.log("info", "tls", f"{host}:{port} doesn't send its intermediate certificate"
                                      f"{'s' if len(fetched) > 1 else ''}; fetched {len(fetched)} from the "
                                      f"addresses in its certificate (as browsers do)")
                return True
    except Exception as e:
        db.log("warn", "tls", f"couldn't fetch the intermediate certificates {host}:{port} leaves out: {e}")
    return False


def explain(e: BaseException) -> str:
    """A plain-language reason for a certificate check that still fails."""
    if missing_issuer(e):
        return ("the server's certificate couldn't be checked: it doesn't send its intermediate certificate and "
                "MediaMan couldn't fetch it. Fix the server's certificate chain, or turn off the certificate check")
    return str(e)


def request(method: str, url: str, enabled, **kw) -> httpx.Response:
    """httpx request with the certificate check `enabled`; if the server leaves out its intermediates, they're
    fetched once and the request retried."""
    try:
        return httpx.request(method, url, verify=verify(enabled), **kw)
    except httpx.ConnectError as e:
        if not enabled or not missing_issuer(e) or not complete_chain(url):
            raise
    return httpx.request(method, url, verify=verify(enabled), **kw)
