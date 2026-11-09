"""Optional network integrations: TMDb lookups, Jellyfin library refresh, qBittorrent state."""
from __future__ import annotations

import difflib
import json
import time

import httpx

from . import config, db
from .identify import COUNTRY_SUFFIX, norm

TMDB = "https://api.themoviedb.org/3"


def _tmdb_get(path: str, params: dict) -> dict | None:
    key = config.get("tmdb_api_key")
    if not key:
        return None
    cache_key = path + "?" + json.dumps(params, sort_keys=True)
    row = db.one("SELECT data, ts FROM tmdb_cache WHERE q=?", (cache_key,))
    if row and time.time() - row["ts"] < 30 * 86400:
        return db.jload(row["data"])
    headers, p = {}, dict(params)
    if len(key) > 40:          # v4 read access token
        headers["Authorization"] = f"Bearer {key}"
    else:                      # v3 api key
        p["api_key"] = key
    try:
        r = httpx.get(TMDB + path, params=p, headers=headers, timeout=15)
        if r.status_code == 404:
            data = {}
        else:
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        db.log("warn", "tmdb", f"lookup failed for {path}: {e}")
        return None
    db.x("INSERT OR REPLACE INTO tmdb_cache(q,data,ts) VALUES(?,?,?)", (cache_key, json.dumps(data), time.time()))
    return data


def _pick(results: list[dict], title: str, year: int | None, tkey: str, okey: str, dkey: str) -> dict | None:
    best, best_score = None, 0.0
    want = norm(title)
    for i, r in enumerate(results[:10]):
        names = {norm(r.get(tkey)), norm(r.get(okey))}
        sim = max(difflib.SequenceMatcher(None, want, n).ratio() for n in names if n is not None)
        ryear = int(r[dkey][:4]) if r.get(dkey) and r[dkey][:4].isdigit() else None
        s = sim
        if year and ryear:
            s += 0.25 if ryear == year else (0.1 if abs(ryear - year) == 1 else -0.3)
        s -= i * 0.02          # TMDb's own ranking (popularity) breaks ties
        if s > best_score:
            best, best_score = r, s
    return best if best and best_score >= 0.8 else None


def enrich(info: dict) -> dict:
    """Confirm/canonicalise an identification against TMDb. No-op without an API key."""
    if not config.get("tmdb_api_key") or not info.get("title"):
        return info
    ids = info.setdefault("ids", {})
    if info["type"] == "movie":
        hit = None
        if ids.get("tmdb"):
            hit = _tmdb_get(f"/movie/{ids['tmdb']}", {})
        elif ids.get("imdb"):
            found = _tmdb_get(f"/find/{ids['imdb']}", {"external_source": "imdb_id"}) or {}
            hit = (found.get("movie_results") or [None])[0]
        if not hit:
            params = {"query": info["title"]}
            if info.get("year"):
                params["year"] = info["year"]
            res = _tmdb_get("/search/movie", params)
            if res is None:
                return info
            results = res.get("results", [])
            if not results and info.get("year"):
                results = (_tmdb_get("/search/movie", {"query": info["title"]}) or {}).get("results", [])
            hit = _pick(results, info["title"], info.get("year"), "title", "original_title", "release_date")
        if hit and hit.get("id"):
            info["title"] = hit.get("title") or info["title"]
            if (hit.get("release_date") or "")[:4].isdigit():
                info["year"] = int(hit["release_date"][:4])
            ids["tmdb"] = str(hit["id"])
            info["tmdb_match"] = True
        else:
            info["tmdb_match"] = False
    else:
        hit = None
        if ids.get("tmdb"):
            hit = _tmdb_get(f"/tv/{ids['tmdb']}", {})
        elif ids.get("tvdb") or ids.get("imdb"):
            src, val = ("tvdb_id", ids["tvdb"]) if ids.get("tvdb") else ("imdb_id", ids["imdb"])
            found = _tmdb_get(f"/find/{val}", {"external_source": src}) or {}
            hit = (found.get("tv_results") or [None])[0]
        if not hit:
            params = {"query": COUNTRY_SUFFIX.sub("", info["title"])}
            if info.get("year"):
                params["first_air_date_year"] = info["year"]
            res = _tmdb_get("/search/tv", params)
            if res is None:
                return info
            results = res.get("results", [])
            if not results and info.get("year"):
                results = (_tmdb_get("/search/tv", {"query": params["query"]}) or {}).get("results", [])
            hit = _pick(results, COUNTRY_SUFFIX.sub("", info["title"]), info.get("year"), "name", "original_name",
                        "first_air_date")
        if hit and hit.get("id"):
            info["title"] = hit.get("name") or info["title"]
            if (hit.get("first_air_date") or "")[:4].isdigit():
                info["year"] = int(hit["first_air_date"][:4])
            ids["tmdb"] = str(hit["id"])
            info["tmdb_match"] = True
            if info.get("episodes") and info.get("season") is not None:
                ep = _tmdb_get(f"/tv/{hit['id']}/season/{info['season']}/episode/{info['episodes'][0]}", {})
                if ep and ep.get("name"):
                    info["episode_title"] = ep["name"]
                elif ep == {}:
                    info.setdefault("signals", []).append("episode-not-on-tmdb")
                    info["tmdb_match"] = None
        else:
            info["tmdb_match"] = False
    return info


def tmdb_test() -> str:
    r = _tmdb_get("/configuration", {})
    return "ok" if r else "failed (check the key)"


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
        r = httpx.post(url + "/Library/Refresh", headers=_jf_headers(), timeout=15,
                       verify=config.get("jellyfin_verify_tls"))
        if r.status_code >= 400:
            db.log("warn", "jellyfin", f"refresh failed: {_jf_explain(r)}")
            return False
        db.log("info", "jellyfin", "library refresh requested")
        return True
    except Exception as e:
        db.log("warn", "jellyfin", f"refresh failed: {e}")
        return False


def jellyfin_test() -> str:
    url, key = _jf_url(), config.get("jellyfin_api_key")
    if not url or not key:
        return "not configured"
    try:
        pub = httpx.get(url + "/System/Info/Public", timeout=10, verify=config.get("jellyfin_verify_tls"))
        pub.raise_for_status()
        r = httpx.get(url + "/System/Info", headers=_jf_headers(), timeout=10, verify=config.get("jellyfin_verify_tls"))
        if r.status_code >= 400:
            return f"reached {pub.json().get('ServerName')} (Jellyfin {pub.json().get('Version')}) but {_jf_explain(r)}"
        j = r.json()
        return f"ok — {j.get('ServerName')} {j.get('Version')}"
    except Exception as e:
        return f"failed: {e}"


# ---------------------------------------------------------------- qBittorrent
qbit_state = {"error": None}


def _qbit_client() -> tuple[httpx.Client, str]:
    url = config.get("qbittorrent_url").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    c = httpx.Client(timeout=10, verify=False, headers={"Referer": url, "Origin": url})
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
            qbit_state["error"] = None
            return {t["name"] for t in r.json() if t.get("progress", 0) >= 1.0}
        finally:
            c.close()
    except Exception as e:
        msg = str(e) if isinstance(e, PermissionError) else f"unreachable: {e}"
        if qbit_state["error"] != msg:      # log each distinct problem once, not every poll
            db.log("warn", "qbittorrent", msg)
        qbit_state["error"] = msg
        return None


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
