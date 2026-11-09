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
