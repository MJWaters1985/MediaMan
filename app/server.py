"""Container entry point: start uvicorn over HTTP or HTTPS."""
import os

import uvicorn

from . import tls


def main():
    st = tls.resolve()
    kw = {}
    if st["enabled"]:
        kw = {"ssl_certfile": tls.CERT, "ssl_keyfile": tls.KEY}
        print(f"[info] tls: serving HTTPS with {tls.CERT}", flush=True)
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("MEDIAMAN_PORT", "8765")),
                access_log=False, **kw)


if __name__ == "__main__":
    main()
