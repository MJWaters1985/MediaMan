"""Docker HEALTHCHECK: probe /healthz over HTTPS (cert not verified for 127.0.0.1) or HTTP."""
import os
import ssl
import sys
import urllib.request

port = os.environ.get("MEDIAMAN_PORT", "8765")
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
for url in (f"https://127.0.0.1:{port}/healthz", f"http://127.0.0.1:{port}/healthz"):
    try:
        urllib.request.urlopen(url, timeout=4, context=ctx if url.startswith("https") else None)
        sys.exit(0)
    except Exception:
        pass
sys.exit(1)
