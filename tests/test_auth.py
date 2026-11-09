import base64
import os
import stat
import tempfile

os.environ["MEDIAMAN_CONFIG"] = tempfile.mkdtemp()
os.environ.pop("MEDIAMAN_PASSWORD", None)

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, main  # noqa: E402

client = TestClient(main.app)       # no `with`: startup (worker, hardware probe) isn't needed here


def basic(pw, user="anyone"):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def test_password_set_change_remove():
    assert client.get("/api/settings").status_code == 200                       # off by default
    r = client.post("/api/auth/password", json={"new": "short"})
    assert r.status_code == 400 and "8 characters" in r.json()["detail"]
    assert client.post("/api/auth/password", json={"new": "first-password"}).json() == {"enabled": True}

    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/settings", headers=basic("nope-nope")).status_code == 401
    assert client.get("/api/settings", headers=basic("first-password")).status_code == 200
    assert client.get("/api/settings", headers=basic("first-password", "other-user")).status_code == 200
    assert client.get("/healthz").status_code == 200                            # health check stays open

    f = auth._file()
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert "first-password" not in open(f).read()
    assert "first-password" not in client.get("/api/settings", headers=basic("first-password")).text

    h = basic("first-password")
    r = client.post("/api/auth/password", headers=h, json={"current": "wrong-one", "new": "second-password"})
    assert r.status_code == 400 and "wrong" in r.json()["detail"]
    assert client.post("/api/auth/password", headers=h,
                       json={"current": "first-password", "new": "second-password"}).status_code == 200
    assert client.get("/api/settings", headers=h).status_code == 401           # old password stops working at once
    h = basic("second-password")
    assert client.get("/api/status", headers=h).json()["auth"]["enabled"] is True

    assert client.post("/api/auth/password", headers=h, json={"current": "second-password", "new": ""}).status_code == 200
    assert client.get("/api/settings").status_code == 200 and not os.path.exists(f)


def test_environment_password_is_ignored(monkeypatch):
    """MEDIAMAN_PASSWORD was dropped: it must not lock or unlock anything, and Settings stays in charge."""
    monkeypatch.setenv("MEDIAMAN_PASSWORD", "from-the-yaml")
    assert auth.state() == {"enabled": False}
    assert client.get("/api/settings").status_code == 200
    assert client.post("/api/auth/password", json={"new": "settings-password"}).status_code == 200
    assert client.get("/api/settings", headers=basic("from-the-yaml")).status_code == 401
    h = basic("settings-password")
    assert client.get("/api/settings", headers=h).status_code == 200
    assert client.post("/api/auth/password", headers=h, json={"current": "settings-password", "new": ""}).status_code == 200


def test_changes_from_other_sites_are_refused():
    url = "/api/queue/pause"
    try:
        # browsers label the request; anything not from MediaMan's own page is refused
        for h in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                  {"Origin": "https://evil.example"}, {"Origin": "null"}):
            r = client.post(url, headers=h)
            assert r.status_code == 403, h
        assert client.put("/api/settings", headers={"Origin": "https://evil.example"}, json={}).status_code == 403
        # MediaMan's own page, the browser's address bar, and non-browser clients still work
        assert client.post(url, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
        assert client.post(url, headers={"Sec-Fetch-Site": "none"}).status_code == 200
        assert client.post(url, headers={"Origin": "http://testserver"}).status_code == 200
        assert client.post(url, headers={"Origin": "https://proxy.example", "X-Forwarded-Host": "proxy.example"}).status_code == 200
        assert client.post(url).status_code == 200
        # reading is never blocked (a page can't read the answer anyway)
        assert client.get("/api/status", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    finally:
        client.post("/api/queue/resume")
