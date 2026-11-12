"""A server that leaves out its intermediate certificate, as Jellyfin often does: MediaMan fetches it from the
CA Issuers address in the server's certificate (as browsers do), and the check stays strict."""
import os
import shutil
import ssl
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("MEDIAMAN_CONFIG", tempfile.mkdtemp())

import httpx  # noqa: E402
import pytest  # noqa: E402

from app import tlschain  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("openssl"), reason="needs the openssl command")


def _openssl(*args, cwd):
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True)


def _serve(handler, ctx=None):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    if ctx:
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture()
def pki(tmp_path):
    """root CA → intermediate (whose DER is served over http) → leaf for 127.0.0.1 naming the intermediate's URL."""
    d = str(tmp_path)
    files = {}

    class Issuers(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = files.get(self.path, b"")
            self.send_response(200 if body else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    http = _serve(Issuers)
    aia = f"http://127.0.0.1:{http.server_port}/int.cer"
    open(f"{d}/ca.ext", "w").write("basicConstraints=critical,CA:true\nkeyUsage=critical,keyCertSign,cRLSign\n")
    open(f"{d}/leaf.ext", "w").write(f"subjectAltName=IP:127.0.0.1\nauthorityInfoAccess=caIssuers;URI:{aia}\n")
    for name in ("root", "int", "leaf"):
        _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", f"{name}.key", cwd=d)
    _openssl("req", "-x509", "-new", "-key", "root.key", "-subj", "/CN=Test Root", "-days", "2",
             "-addext", "basicConstraints=critical,CA:true", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
             "-out", "root.pem", cwd=d)
    _openssl("req", "-new", "-key", "int.key", "-subj", "/CN=Test Intermediate", "-out", "int.csr", cwd=d)
    _openssl("x509", "-req", "-in", "int.csr", "-CA", "root.pem", "-CAkey", "root.key", "-CAcreateserial",
             "-days", "2", "-extfile", "ca.ext", "-out", "int.pem", cwd=d)
    _openssl("req", "-new", "-key", "leaf.key", "-subj", "/CN=127.0.0.1", "-out", "leaf.csr", cwd=d)
    _openssl("x509", "-req", "-in", "leaf.csr", "-CA", "int.pem", "-CAkey", "int.key", "-CAcreateserial",
             "-days", "2", "-extfile", "leaf.ext", "-out", "leaf.pem", cwd=d)
    files["/int.cer"] = ssl.PEM_cert_to_DER_cert(open(f"{d}/int.pem").read())

    class Hello(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(f"{d}/leaf.pem", f"{d}/leaf.key")      # the leaf only: no intermediate sent
    https = _serve(Hello, sctx)
    yield {"dir": d, "url": f"https://127.0.0.1:{https.server_port}/", "aia": aia, "files": files}
    https.shutdown()
    http.shutdown()


def test_reads_the_ca_issuers_address(pki):
    der = ssl.PEM_cert_to_DER_cert(open(os.path.join(pki["dir"], "leaf.pem")).read())
    assert tlschain.ca_issuers_url(der) == pki["aia"]
    root = ssl.PEM_cert_to_DER_cert(open(os.path.join(pki["dir"], "root.pem")).read())
    assert tlschain.ca_issuers_url(root) is None


def test_missing_intermediate_is_fetched_and_kept(pki, monkeypatch, tmp_path):
    monkeypatch.setattr(tlschain.certifi, "where", lambda: os.path.join(pki["dir"], "root.pem"))
    monkeypatch.setattr(tlschain, "cache_file", lambda: str(tmp_path / "intermediates.pem"))
    tlschain._ctx["ctx"] = None
    with pytest.raises(httpx.ConnectError) as e:          # plain httpx: the chain can't be built
        httpx.get(pki["url"], verify=tlschain._new_context())
    assert tlschain.missing_issuer(e.value)

    r = tlschain.request("GET", pki["url"], enabled=True, timeout=10)
    assert r.text == "ok"
    assert open(tmp_path / "intermediates.pem").read().count("BEGIN CERTIFICATE") == 1

    pki["files"].clear()                                  # kept: later calls don't fetch it again
    assert tlschain.request("GET", pki["url"], enabled=True, timeout=10).text == "ok"
    assert open(tmp_path / "intermediates.pem").read().count("BEGIN CERTIFICATE") == 1


def test_an_untrusted_root_still_fails(pki, monkeypatch, tmp_path):
    """Fetching intermediates never makes an unknown root trusted."""
    monkeypatch.setattr(tlschain, "cache_file", lambda: str(tmp_path / "intermediates.pem"))   # real roots only
    tlschain._ctx["ctx"] = None
    with pytest.raises(httpx.ConnectError) as e:
        tlschain.request("GET", pki["url"], enabled=True, timeout=10)
    assert "certificate" in tlschain.explain(e.value)
    assert not os.path.exists(tmp_path / "intermediates.pem")
    assert tlschain.request("GET", pki["url"], enabled=False, timeout=10).text == "ok"   # check turned off
