import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ["MEDIAMAN_CONFIG"] = tempfile.mkdtemp()

from app import config, integrations  # noqa: E402


class FakeQbit(BaseHTTPRequestHandler):
    """Mimics qBittorrent 5.x: 204 on good login, 401 on bad, 403 without the SID cookie."""
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="text/plain", cookie=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode()
        if "password=good" in body:
            self._send(204, cookie="SID=abc; HttpOnly; path=/")
        else:
            self._send(401, b"Unauthorized")

    def do_GET(self):
        if "SID=abc" not in (self.headers.get("Cookie") or ""):
            return self._send(403, b"Forbidden")
        if self.path.endswith("/app/version"):
            return self._send(200, b"v5.2.0")
        if self.path.endswith("/torrents/info"):
            data = [{"name": "Done.Movie.2020", "progress": 1.0}, {"name": "Partial", "progress": 0.4}]
            return self._send(200, json.dumps(data).encode(), "application/json")
        self._send(404)


def test_qbit_new_style_login():
    srv = HTTPServer(("127.0.0.1", 0), FakeQbit)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    config.update({"qbittorrent_url": url, "qbittorrent_username": "u", "qbittorrent_password": "good"})
    assert integrations.qbit_test() == "ok — qBittorrent v5.2.0, 1 completed torrents"
    assert integrations.qbit_completed_names() == {"Done.Movie.2020"}
    config.update({"qbittorrent_password": "bad"})
    assert "rejected" in integrations.qbit_test()
    assert integrations.qbit_completed_names() is None
    srv.shutdown()


class FakeJellyfin(BaseHTTPRequestHandler):
    """Mimics Jellyfin's /Items/RemoteSearch/{Movie,Series} (results in TMDb's order, plus OMDb rows without a Tmdb id)."""
    DB = {"Movie": [("Dune", 2021, "438631"), ("Dune", 1984, "841"), ("Spider-Man: No Way Home", 2021, "634649"),
                    ("Heat", 1995, "949")],
          "Series": [("The Office", 2005, "2316"), ("The Office", 2001, "2996")]}
    IMDB = {"tt0113277": "949"}
    calls = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        if 'Token="good"' not in (self.headers.get("Authorization") or ""):
            self.send_response(401)
            self.end_headers()
            return
        kind = self.path.rsplit("/", 1)[1]
        si = body["SearchInfo"]
        FakeJellyfin.calls.append((kind, si["Name"], si["Year"], si["ProviderIds"]))
        ids = si["ProviderIds"]
        rows = self.DB.get(kind, [])
        if ids.get("Tmdb"):
            rows = [r for r in rows if r[2] == ids["Tmdb"]]
        elif ids.get("Imdb"):
            rows = [r for r in rows if r[2] == self.IMDB.get(ids["Imdb"])]
        else:
            want = si["Name"].lower().replace("-", " ").replace(":", "")
            rows = [r for r in rows if want.split()[0] in r[0].lower()]
            if si["Year"]:
                rows = [r for r in rows if r[1] == si["Year"]] + [r for r in rows if r[1] != si["Year"]]
        out = [{"Name": n, "ProductionYear": y, "ProviderIds": {"Tmdb": t}, "SearchProviderName": "TheMovieDb"}
               for n, y, t in rows] + [{"Name": "noise", "ProductionYear": 2000, "ProviderIds": {"Imdb": "tt1"}}]
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _jellyfin(key="good"):
    srv = HTTPServer(("127.0.0.1", 0), FakeJellyfin)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    config.update({"jellyfin_url": f"http://127.0.0.1:{srv.server_port}", "jellyfin_api_key": key,
                   "identify_with_jellyfin": True})
    from app import db
    db.x("DELETE FROM tmdb_cache")
    return srv


def test_lookup_via_jellyfin():
    srv = _jellyfin()
    try:
        m = integrations.enrich({"type": "movie", "title": "Dune", "year": 1984})
        assert (m["ids"]["tmdb"], m["year"], m["tmdb_match"]) == ("841", 1984, True)
        m = integrations.enrich({"type": "movie", "title": "Spider Man No Way Home", "year": 2021})
        assert m["title"] == "Spider-Man: No Way Home" and m["ids"]["tmdb"] == "634649"
        m = integrations.enrich({"type": "movie", "title": "Some Release", "ids": {"imdb": "tt0113277"}})
        assert (m["title"], m["year"], m["ids"]["tmdb"]) == ("Heat", 1995, "949")
        s = integrations.enrich({"type": "episode", "title": "The Office (US)", "season": 2, "episodes": [3]})
        assert s["ids"]["tmdb"] == "2316" and s["title"] == "The Office"
        n = integrations.enrich({"type": "movie", "title": "Zzqx Nothing", "year": 2019})
        assert n["tmdb_match"] is False and "tmdb" not in n["ids"]
        # cached: the same question isn't asked twice
        before = len(FakeJellyfin.calls)
        integrations.enrich({"type": "movie", "title": "Dune", "year": 1984})
        assert len(FakeJellyfin.calls) == before
    finally:
        srv.shutdown()


def test_lookup_off_or_failing_changes_nothing():
    srv = _jellyfin(key="bad")
    try:
        info = integrations.enrich({"type": "movie", "title": "Dune", "year": 2021})
        assert "tmdb_match" not in info and not info.get("ids", {}).get("tmdb")      # 401: left as identified
        config.update({"jellyfin_api_key": "good", "identify_with_jellyfin": False})
        before = len(FakeJellyfin.calls)
        info = integrations.enrich({"type": "movie", "title": "Dune", "year": 2021})
        assert "tmdb_match" not in info and len(FakeJellyfin.calls) == before       # switched off: not asked
    finally:
        srv.shutdown()


def test_old_tmdb_key_is_dropped(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"settings_version": 2, "tmdb_api_key": "secret-value", "movies_dir": "/x"}))
    monkeypatch.setattr(config, "SETTINGS_FILE", str(f))
    monkeypatch.setattr(config, "_settings", None)
    assert "tmdb_api_key" not in config.public() and config.get("movies_dir") == "/x"
    assert "secret-value" not in f.read_text()
    monkeypatch.setattr(config, "_settings", None)



def test_paths_are_translated_for_jellyfin():
    mine = ["/data/Media/Movies", "/data/Media/Shows", "/pool2/4K"]
    theirs = ["/Media/Movies", "/Media/Shows", "/config/data/collections", "/data/4K"]
    m = integrations.map_to_jellyfin
    assert m("/data/Media/Movies/Dune (2021)/Dune (2021).mkv", mine, theirs) == "/Media/Movies/Dune (2021)/Dune (2021).mkv"
    assert m("/data/Media/Shows/X/Season 01/X S01E01.mkv", mine, theirs) == "/Media/Shows/X/Season 01/X S01E01.mkv"
    assert m("/pool2/4K/A/A.mkv", mine, theirs) == "/data/4K/A/A.mkv"
    assert m("/elsewhere/a.mkv", mine, theirs) is None
    assert m("/data/Media/Movies/a.mkv", mine, ["/a/Movies", "/b/Movies"]) is None     # ambiguous


class FakeJellyfinLibrary(BaseHTTPRequestHandler):
    posts = []

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/Library/VirtualFolders"):
            return self._json([{"Name": "Movies", "Locations": ["/Media/Movies"]}])
        self._json({}, 404)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        FakeJellyfinLibrary.posts.append((self.path, json.loads(body or b"null")))
        self.send_response(204)
        self.end_headers()


def test_jellyfin_told_only_about_changed_files(tmp_path):
    srv = HTTPServer(("127.0.0.1", 0), FakeJellyfinLibrary)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    movies = str(tmp_path / "Share" / "Media" / "Movies")
    config.update({"jellyfin_url": f"http://127.0.0.1:{srv.server_port}", "jellyfin_api_key": "good",
                   "movies_dir": movies})
    integrations._folders.update(ts=0)
    try:
        integrations.jellyfin_changed(movies + "/Dune (2021)/Dune (2021).mkv", "Modified")
        integrations.jellyfin_changed(movies + "/Old/Old.mkv", "Deleted")
        assert integrations.jellyfin_flush() is True and not integrations.jellyfin_pending()
        path, body = FakeJellyfinLibrary.posts[-1]
        assert path == "/Library/Media/Updated"
        assert sorted((u["Path"], u["UpdateType"]) for u in body["Updates"]) == [
            ("/Media/Movies/Dune (2021)/Dune (2021).mkv", "Modified"), ("/Media/Movies/Old/Old.mkv", "Deleted")]
        # a file outside Jellyfin's folders: whole-library refresh instead
        integrations.jellyfin_changed("/nowhere/x.mkv", "Created")
        integrations.jellyfin_flush()
        assert FakeJellyfinLibrary.posts[-1][0] == "/Library/Refresh"
    finally:
        srv.shutdown()
