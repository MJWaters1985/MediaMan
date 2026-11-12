"""Optional network integrations: title lookups and library refresh via Jellyfin, qBittorrent state."""
from __future__ import annotations

import difflib
import json
import threading
import time

import httpx

from . import config, db, tlschain
from .identify import COUNTRY_SUFFIX, norm

def lookups_enabled() -> bool:
    return bool(config.get("identify_with_jellyfin") and _jf_url() and config.get("jellyfin_api_key"))


def _jf_search(kind: str, name: str, year: int | None, ids: dict) -> list[dict] | None:
    """Title search through Jellyfin's own metadata providers (its TMDb plugin, with Jellyfin's key), so MediaMan
    needs no TMDb account. Returns TMDb hits as {id, title, year} in TMDb's ranking; None if Jellyfin can't be asked."""
    if not lookups_enabled():
        return None
    info = {"Name": name or "", "Year": year, "ProviderIds": ids}
    cache_key = "jf:" + kind + json.dumps(info, sort_keys=True)
    row = db.one("SELECT data, ts FROM tmdb_cache WHERE q=?", (cache_key,))
    if row and time.time() - row["ts"] < 30 * 86400:
        return db.jload(row["data"])
    try:
        r = tlschain.request("POST", _jf_url() + f"/Items/RemoteSearch/{kind}", headers=_jf_headers(), timeout=30,
                             json={"SearchInfo": info, "IncludeDisabledProviders": True},
                             enabled=config.get("jellyfin_verify_tls"))
        if r.status_code >= 400:
            db.log("warn", "lookup", f"Jellyfin search failed for {name!r}: {_jf_explain(r)}")
            return None
        data = [{"id": str(x["ProviderIds"]["Tmdb"]), "title": x.get("Name") or "", "year": x.get("ProductionYear")}
                for x in r.json() if (x.get("ProviderIds") or {}).get("Tmdb")]
    except Exception as e:
        db.log("warn", "lookup", f"Jellyfin search failed for {name!r}: {tlschain.explain(e)}")
        return None
    db.x("INSERT OR REPLACE INTO tmdb_cache(q,data,ts) VALUES(?,?,?)", (cache_key, json.dumps(data), time.time()))
    return data


def _pick(results: list[dict], title: str, year: int | None) -> dict | None:
    best, best_score = None, 0.0
    want = norm(title)
    for i, r in enumerate(results[:10]):
        s = difflib.SequenceMatcher(None, want, norm(r["title"])).ratio()
        if year and r.get("year"):
            s += 0.25 if r["year"] == year else (0.1 if abs(r["year"] - year) == 1 else -0.3)
        s -= i * 0.02          # TMDb's own ranking (popularity) breaks ties
        if s > best_score:
            best, best_score = r, s
    return best if best and best_score >= 0.8 else None


def enrich(info: dict) -> dict:
    """Confirm/canonicalise an identification against TMDb, asked through Jellyfin. No-op when Jellyfin isn't set up."""
    if not lookups_enabled() or not info.get("title"):
        return info
    ids = info.setdefault("ids", {})
    movie = info["type"] == "movie"
    kind = "Movie" if movie else "Series"
    title = info["title"] if movie else COUNTRY_SUFFIX.sub("", info["title"])
    hit = None
    known = {k.capitalize(): ids[k] for k in ("tmdb", "imdb", "tvdb") if ids.get(k)}
    if known:
        res = _jf_search(kind, title, info.get("year"), known)
        if res is None:
            return info
        if ids.get("tmdb"):
            hit = next((r for r in res if r["id"] == str(ids["tmdb"])), None)
        else:
            hit = res[0] if res else None
    if not hit:
        res = _jf_search(kind, title, info.get("year"), {})
        if res is None:
            return info
        if not res and info.get("year"):
            res = _jf_search(kind, title, None, {}) or []
        hit = _pick(res, title, info.get("year"))
    if hit:
        info["title"] = hit["title"] or info["title"]
        if hit.get("year"):
            info["year"] = int(hit["year"])
        ids["tmdb"] = hit["id"]
        info["tmdb_match"] = True
    else:
        info["tmdb_match"] = False
    return info


# ---------------------------------------------------------------- Jellyfin
def _jf_headers() -> dict:
    # Jellyfin 10.11+ disables the legacy X-Emby-Token header by default; use the documented scheme.
    key = config.get("jellyfin_api_key")
    return {"Authorization": f'MediaBrowser Client="MediaMan", Device="MediaMan", DeviceId="mediaman", '
                             f'Version="1.0.0", Token="{key}"'}


def _jf_url() -> str:
    url = (config.get("jellyfin_url") or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _jf_explain(r: httpx.Response) -> str:
    if r.status_code == 401:
        return "401 Unauthorized — Jellyfin rejected the API key (create one under Dashboard → API Keys)"
    if r.status_code == 403:
        return "403 Forbidden — the key works but isn't allowed to do this"
    return f"HTTP {r.status_code}"


def jellyfin_refresh() -> bool:
    url, key = _jf_url(), config.get("jellyfin_api_key")
    if not url or not key:
        return False
    try:
        r = tlschain.request("POST", url + "/Library/Refresh", headers=_jf_headers(), timeout=15,
                             enabled=config.get("jellyfin_verify_tls"))
        if r.status_code >= 400:
            db.log("warn", "jellyfin", f"refresh failed: {_jf_explain(r)}")
            return False
        db.log("info", "jellyfin", "library refresh requested")
        return True
    except Exception as e:
        db.log("warn", "jellyfin", f"refresh failed: {tlschain.explain(e)}")
        return False


_changes: dict[str, str] = {}
_changes_lock = threading.Lock()
_folders = {"ts": 0.0, "locations": []}


def jellyfin_changed(path: str, kind: str = "Modified") -> None:
    """Remember a library change (Created / Modified / Deleted) for the next batched Jellyfin update."""
    if path:
        with _changes_lock:
            _changes[path] = kind


def jellyfin_pending() -> bool:
    return bool(_changes)


def _jf_locations() -> list[str]:
    """Jellyfin's own library folders (its paths can differ from MediaMan's: other container, other mounts)."""
    if time.time() - _folders["ts"] < 600:
        return _folders["locations"]
    r = tlschain.request("GET", _jf_url() + "/Library/VirtualFolders", headers=_jf_headers(), timeout=15,
                         enabled=config.get("jellyfin_verify_tls"))
    r.raise_for_status()
    locs = [loc for f in r.json() for loc in (f.get("Locations") or [])]
    _folders.update(ts=time.time(), locations=locs)
    return locs


def map_to_jellyfin(path: str, mine: list[str], theirs: list[str]) -> str | None:
    """The same file as Jellyfin sees it. A MediaMan library folder matches the Jellyfin folder with the longest
    run of identical trailing folder names (/data/Media/Movies ↔ /Media/Movies); ties or no match → None."""
    for root in sorted(mine, key=len, reverse=True):
        r = root.rstrip("/")
        if path != r and not path.startswith(r + "/"):
            continue
        rp = [p for p in r.split("/") if p]
        best, best_n, tie = None, 0, False
        for loc in theirs:
            lp = [p for p in loc.rstrip("/").split("/") if p]
            n = 0
            while n < min(len(rp), len(lp)) and rp[-1 - n] == lp[-1 - n]:
                n += 1
            if n > best_n:
                best, best_n, tie = loc.rstrip("/"), n, False
            elif n == best_n and n:
                tie = True
        if best and not tie:
            return best + path[len(r):]
        return None
    return None


def jellyfin_flush() -> bool:
    """Tell Jellyfin exactly which files changed (it rescans just those folders). Falls back to a full library
    refresh when a path can't be matched to one of Jellyfin's folders."""
    with _changes_lock:
        changes = dict(_changes)
        _changes.clear()
    url, key = _jf_url(), config.get("jellyfin_api_key")
    if not changes or not url or not key:
        return False
    from .organizer import library_dirs
    mine = library_dirs("movies") + library_dirs("tv")
    try:
        theirs = _jf_locations()
        updates = []
        for p, kind in changes.items():
            jp = map_to_jellyfin(p, mine, theirs)
            if jp is None:
                db.log("info", "jellyfin", f"couldn't match {p} to a Jellyfin library folder — refreshing the "
                                           f"whole library instead")
                return jellyfin_refresh()
            updates.append({"Path": jp, "UpdateType": kind})
        r = tlschain.request("POST", url + "/Library/Media/Updated", headers=_jf_headers(), json={"Updates": updates},
                             timeout=15, enabled=config.get("jellyfin_verify_tls"))
        if r.status_code >= 400:
            db.log("warn", "jellyfin", f"update failed: {_jf_explain(r)} — refreshing the whole library instead")
            return jellyfin_refresh()
        db.log("info", "jellyfin", f"told Jellyfin about {len(updates)} changed file{'s' if len(updates) != 1 else ''}")
        return True
    except Exception as e:
        db.log("warn", "jellyfin", f"update failed: {tlschain.explain(e)} — refreshing the whole library instead")
        return jellyfin_refresh()


def jellyfin_test() -> str:
    url, key = _jf_url(), config.get("jellyfin_api_key")
    if not url or not key:
        return "not configured"
    try:
        pub = tlschain.request("GET", url + "/System/Info/Public", timeout=10, enabled=config.get("jellyfin_verify_tls"))
        pub.raise_for_status()
        r = tlschain.request("GET", url + "/System/Info", headers=_jf_headers(), timeout=10,
                             enabled=config.get("jellyfin_verify_tls"))
        if r.status_code >= 400:
            return f"reached {pub.json().get('ServerName')} (Jellyfin {pub.json().get('Version')}) but {_jf_explain(r)}"
        j = r.json()
        return f"ok — {j.get('ServerName')} {j.get('Version')}"
    except Exception as e:
        return f"failed: {tlschain.explain(e)}"


# ---------------------------------------------------------------- qBittorrent
qbit_state = {"error": None, "failing_since": None, "warned": False}
# a single failed poll (a slow TLS handshake, a DNS hiccup) is retried 30 s later; only a problem that lasts this
# long is logged
QBIT_GRACE_SECONDS = 180


def _qbit_client() -> tuple[httpx.Client, str]:
    url = config.get("qbittorrent_url").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    c = httpx.Client(timeout=httpx.Timeout(20, connect=20), verify=False, headers={"Referer": url, "Origin": url})
    if config.get("qbittorrent_username"):
        r = c.post(url + "/api/v2/auth/login", data={
            "username": config.get("qbittorrent_username"), "password": config.get("qbittorrent_password")})
        body = r.text.strip()
        # qBittorrent < 5: 200 "Ok." / 200 "Fails."; newer: 200/204 on success, 401 on bad credentials
        if r.status_code == 401 or body == "Fails.":
            raise PermissionError("username or password rejected by qBittorrent")
        if r.status_code == 403:
            raise PermissionError("qBittorrent refused the login (403): this IP may be temporarily banned after "
                                  "failed attempts, or the Web UI's CSRF / host-header protection blocked it")
        if r.status_code >= 400:
            raise PermissionError(f"qBittorrent login failed: HTTP {r.status_code} {body[:80]}")
    v = c.get(url + "/api/v2/app/version")
    if v.status_code == 403:
        raise PermissionError("logged in, but qBittorrent still refuses API calls (403)")
    v.raise_for_status()
    return c, url


def qbit_completed_names() -> set[str] | None:
    """Names of torrents qBittorrent reports as fully downloaded, or None if not configured/unreachable."""
    if not config.get("qbittorrent_url"):
        return None
    try:
        c, url = _qbit_client()
        try:
            r = c.get(url + "/api/v2/torrents/info")
            r.raise_for_status()
            names = {t["name"] for t in r.json() if t.get("progress", 0) >= 1.0}
        finally:
            c.close()
    except Exception as e:
        _qbit_failed(str(e) if isinstance(e, PermissionError) else f"unreachable: {e}", isinstance(e, PermissionError))
        return None
    if qbit_state["warned"]:
        mins = (time.time() - qbit_state["failing_since"]) / 60
        db.log("info", "qbittorrent", f"reachable again (after {mins:.0f} min)")
    qbit_state.update(error=None, failing_since=None, warned=False)
    return names


def _qbit_failed(msg: str, permanent: bool) -> None:
    """Log a qBittorrent problem once: a rejected login at once, a connection problem only once it has lasted
    QBIT_GRACE_SECONDS (each poll in between is simply retried)."""
    now = time.time()
    since = qbit_state["failing_since"] = qbit_state["failing_since"] or now
    if permanent or now - since >= QBIT_GRACE_SECONDS:
        if not qbit_state["warned"] or qbit_state["error"] != msg:
            db.log("warn", "qbittorrent", msg if permanent else f"{msg} (for {(now - since) / 60:.0f} min)")
        qbit_state["warned"] = True
    qbit_state["error"] = msg


def qbit_test() -> str:
    if not config.get("qbittorrent_url"):
        return "not configured"
    try:
        c, url = _qbit_client()
        try:
            ver = c.get(url + "/api/v2/app/version").text.strip()
            n = sum(1 for t in c.get(url + "/api/v2/torrents/info").json() if t.get("progress", 0) >= 1.0)
        finally:
            c.close()
        return f"ok — qBittorrent {ver}, {n} completed torrents"
    except Exception as e:
        return f"failed: {e}"
