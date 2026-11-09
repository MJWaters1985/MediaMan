"""MediaMan web app: REST API + static UI."""
from __future__ import annotations

import asyncio
import os
import threading
import time
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import auth, config, db, fsbrowse, integrations, organizer, probe, tls, transcode, watcher

VERSION = "1.7.2"
STATIC = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="MediaMan", version=VERSION)


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def cross_site(request: Request) -> bool:
    """A change requested by another website (a form or script on a page the user has open). Browsers label every
    request: Sec-Fetch-Site on current ones, Origin on older ones. Clients that send neither (curl, scripts) aren't
    browsers acting for another site, so they're let through to the password check."""
    site = request.headers.get("sec-fetch-site")
    if site:
        return site not in ("same-origin", "none")
    origin = request.headers.get("origin")
    if not origin:
        return False
    if origin == "null":
        return True
    hosts = {request.headers.get("host", "").lower(), request.headers.get("x-forwarded-host", "").lower()} - {""}
    return urlsplit(origin).netloc.lower() not in hosts


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if request.method in UNSAFE_METHODS and cross_site(request):
        return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
    if request.url.path != "/healthz" and not auth.authorized(request.headers.get("authorization", "")):
        if request.headers.get("authorization"):
            await asyncio.sleep(1)          # slow down guessing
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="MediaMan"'})
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-cache"   # revalidate UI files after an app update
    return resp


@app.on_event("startup")
async def startup():
    db.conn()
    db.requeue_interrupted()
    config.load()
    transcode.startup_queue_policy()
    db.log("info", "app", f"MediaMan {VERSION} starting")
    if os.environ.get("MEDIAMAN_PASSWORD"):
        db.log("warn", "app", "MEDIAMAN_PASSWORD is no longer supported and is ignored — set a password under "
                              "Settings → Web UI password" + ("" if auth.state()["enabled"] else " (none is set now)"))
    await asyncio.to_thread(transcode.detect_hardware)
    asyncio.create_task(transcode.worker.loop())
    asyncio.create_task(watcher.loop())
    asyncio.create_task(_housekeeping_loop())
    if tls.state["error"]:
        db.log("error", "tls", f"HTTPS requested but {tls.state['error']} — serving plain HTTP")
    asyncio.create_task(tls.watch_for_renewal(_busy, db.log, int(os.environ.get("MEDIAMAN_TLS_CHECK_SECONDS", "300"))))


async def _housekeeping_loop():
    while True:
        try:
            r = await asyncio.to_thread(db.housekeeping)
            if any(r.values()):
                db.log("info", "app", "housekeeping: removed " + ", ".join(f"{n} old {k}" for k, n in r.items() if n))
            folders, freed = await asyncio.to_thread(transcode.sweep_workdirs, [d for d, _ in organizer.roots()])
            if folders:
                db.log("info", "app", f"housekeeping: removed {folders} leftover transcode work folder"
                                      f"{'s' if folders != 1 else ''} ({freed / 1024 ** 3:.1f} GB)")
        except Exception as e:
            db.log("warn", "app", f"housekeeping failed: {e}")
        await asyncio.sleep(86400)


def _busy() -> bool:
    return bool(transcode.worker.tasks) or organizer.scan_state["running"] or \
        bool(db.one("SELECT 1 FROM imports WHERE status='processing'"))


def _bg(fn, *a):
    threading.Thread(target=fn, args=a, daemon=True).start()


# ---------------------------------------------------------------- status / settings
@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/status")
def status():
    def counts(sql):
        return {r["status"]: r["n"] for r in db.q(sql)}
    def folder(p):
        d = {"path": p, "exists": os.path.isdir(p or ""), "writable": os.access(p or "/nonexistent", os.W_OK)}
        if d["exists"]:
            d["free"] = organizer.free_bytes(p)
        return d
    paths = {}
    for kind, label in (("movies", "movies"), ("tv", "tv")):
        for i, p in enumerate(organizer.library_dirs(kind)):
            paths[label if i == 0 else f"{label} {i + 1}"] = folder(p)
    paths["downloads"] = folder(config.get("downloads_dir"))
    return {
        "version": VERSION,
        "hardware": transcode.hw,
        "target_codec": transcode.target_codec(),
        "scan": organizer.scan_state,
        "watcher": {**watcher.state, "enabled": config.get("watcher_enabled")},
        "queue": {"paused": transcode.worker.paused, "in_window": transcode.in_window(),
                  "counts": counts("SELECT status, COUNT(*) n FROM jobs GROUP BY status")},
        "items": counts("SELECT status, COUNT(*) n FROM items WHERE source='scan' GROUP BY status"),
        "imports": counts("SELECT status, COUNT(*) n FROM imports GROUP BY status"),
        "saved_bytes": (db.one("SELECT SUM(orig_size - new_size) s FROM jobs WHERE status='done'") or {}).get("s") or 0,
        "candidates": (db.one("SELECT COUNT(*) n FROM items WHERE source='scan' AND transcode_hint LIKE '%→%' AND status='ok' "
                                   "AND NOT EXISTS (SELECT 1 FROM transcode_skips k WHERE k.path=items.path AND k.size=items.size)") or {})["n"],
        "paths": paths,
        "tls": {k: v for k, v in tls.state.items() if k != "sig"},
        "ffmpeg": probe.FFMPEG,
        "auth": auth.state(),
    }


@app.get("/api/settings")
def get_settings():
    return config.public()


@app.get("/api/fs")
def browse_folders(path: str = ""):
    """Folder picker for Settings -> Folders: subfolders of a mounted folder (or the list of mounted folders)."""
    try:
        return fsbrowse.listing(path or None)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/auth/password")
def change_password(body: dict = Body(...)):
    """Set, change or remove (empty "new") the web UI password. The current one is required to change it."""
    try:
        auth.set_password(str(body.get("current") or ""), str(body.get("new") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.log("info", "app", "web UI password " + ("set" if body.get("new") else "removed"))
    return auth.state()


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    s = config.update(body)
    if any(k in body for k in ("device_path", "preferred_backend")):
        _bg(transcode.detect_hardware)
    return s


@app.post("/api/settings/test/{what}")
def test_integration(what: str):
    fn = {"jellyfin": integrations.jellyfin_test, "qbittorrent": integrations.qbit_test}.get(what)
    if not fn:
        raise HTTPException(404)
    return {"result": fn()}


@app.post("/api/hardware/detect")
async def redetect():
    return await asyncio.to_thread(transcode.detect_hardware)


# ---------------------------------------------------------------- library items
def _item_out(r: dict) -> dict:
    r = dict(r)
    r["info"] = db.jload(r["info"]) or {}
    k = db.one("SELECT size, reason FROM transcode_skips WHERE path=?", (r["path"],))
    r["skipped"] = k["reason"] if k and k["size"] == r["size"] else None
    j = db.one("SELECT status, progress FROM jobs WHERE path=? AND status IN ('queued','running','paused') "
               "ORDER BY id DESC LIMIT 1", (r["path"],))
    r["job"] = j
    return r


@app.post("/api/scan")
def start_scan(body: dict = Body(default={})):
    if organizer.scan_state["running"]:
        return {"started": False, "reason": "scan already running"}
    _bg(organizer.scan, body.get("lookup", True))
    return {"started": True}


@app.get("/api/items")
def list_items(status: str = "", q: str = "", source: str = "", kind: str = "", transcodable: bool = False,
               offset: int = 0, limit: int = 200):
    where, args = [], []
    if status:
        where.append("status IN (%s)" % ",".join("?" * len(status.split(","))))
        args += status.split(",")
    if source:
        where.append("source=?")
        args.append(source)
    if kind:
        where.append("kind=?")
        args.append(kind)
    if q:
        where.append("(path LIKE ? OR proposed_path LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    if transcodable:
        where.append("transcode_hint LIKE '%→%'")
    sql = "FROM items" + (" WHERE " + " AND ".join(where) if where else "")
    total = db.one("SELECT COUNT(*) n " + sql, args)["n"]
    rows = db.q("SELECT * " + sql + " ORDER BY path LIMIT ? OFFSET ?", args + [limit, offset])
    return {"total": total, "items": [_item_out(r) for r in rows]}


def _sync_import_status(item: dict | None):
    if not item or not item.get("import_id"):
        return
    left = db.one("SELECT COUNT(*) n FROM items WHERE import_id=? AND status IN ('proposed','unknown','conflict','error')",
                  (item["import_id"],))["n"]
    if not left:
        n = db.one("SELECT COUNT(*) n FROM items WHERE import_id=? AND status='applied'", (item["import_id"],))["n"]
        db.x("UPDATE imports SET status='imported', message=?, updated=? WHERE id=?",
             (f"{n} imported", time.time(), item["import_id"]))


@app.post("/api/items/{item_id}/apply")
def apply_item(item_id: int, body: dict = Body(default={})):
    item = db.one("SELECT * FROM items WHERE id=?", (item_id,))
    try:
        res = organizer.apply_item(item_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    except OSError as e:
        raise HTTPException(500, str(e))
    if item and item["source"] == "import":
        _sync_import_status(item)
        if config.get("auto_transcode_imports"):
            transcode.enqueue(res["path"])
    if body.get("transcode"):
        transcode.enqueue(res["path"])
    return res


@app.post("/api/items/apply")
def apply_many(body: dict = Body(...)):
    ids = body.get("ids") or []
    if body.get("min_confidence") is not None:
        ids = [r["id"] for r in db.q("SELECT id FROM items WHERE status='proposed' AND confidence>=?",
                                      (float(body["min_confidence"]),))]
    done, errors = 0, []
    for i in ids:
        try:
            apply_item(int(i), {})
            done += 1
        except HTTPException as e:
            errors.append({"id": i, "error": e.detail})
    return {"applied": done, "errors": errors}


@app.put("/api/items/{item_id}")
def edit_item(item_id: int, body: dict = Body(...)):
    r = organizer.override_item(item_id, body)
    if not r:
        raise HTTPException(404)
    return _item_out(r)


@app.post("/api/items/{item_id}/reidentify")
def reidentify(item_id: int):
    item = db.one("SELECT * FROM items WHERE id=?", (item_id,))
    if not item:
        raise HTTPException(404)
    info = db.jload(item["info"]) or {}
    info.pop("manual", None)
    db.x("UPDATE items SET info=? WHERE id=?", (db.jdump(info), item_id))
    if item["source"] == "import":
        root, kind = config.get("downloads_dir"), None
    else:
        root, kind = organizer.root_for(item["path"])
    return _item_out(organizer.refresh_item(item["path"], item["source"], root, kind, item["import_id"]))


@app.post("/api/items/{item_id}/ignore")
def ignore_item(item_id: int, body: dict = Body(default={})):
    st = "ignored" if body.get("ignore", True) else "proposed"
    db.x("UPDATE items SET status=? WHERE id=?", (st, item_id))
    _sync_import_status(db.one("SELECT * FROM items WHERE id=?", (item_id,)))
    return {"status": st}


@app.get("/api/items/{item_id}/probe")
def item_probe(item_id: int):
    item = db.one("SELECT * FROM items WHERE id=?", (item_id,))
    if not item:
        raise HTTPException(404)
    s = probe.summarize(probe.ffprobe(item["path"]))
    plan = transcode.make_plan(item["path"], s)
    plan.pop("tmp", None)
    for a in plan.get("attempts", []):
        a["cmd"] = " ".join(f"'{c}'" if " " in c else c for c in a["cmd"])
    return {"summary": s, "plan": plan}


# ---------------------------------------------------------------- transcode queue
@app.get("/api/jobs")
def list_jobs(status: str = "", limit: int = 300):
    if status:
        rows = db.q("SELECT * FROM jobs WHERE status IN (%s) ORDER BY id DESC LIMIT ?" % ",".join("?" * len(status.split(","))),
                    status.split(",") + [limit])
    else:
        rows = db.q("""SELECT * FROM jobs ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'paused' THEN 1
                       WHEN 'queued' THEN 2 ELSE 3 END, CASE WHEN status='queued' THEN -priority ELSE 0 END,
                       CASE WHEN status IN ('queued') THEN id ELSE -COALESCE(finished, id) END LIMIT ?""", (limit,))
    for r in rows:
        p = db.jload(r["plan"]) or {}
        r["backend"], r["codec"], r["reason"] = p.get("backend"), p.get("codec"), p.get("reason")
        r.pop("plan")
    return rows


@app.post("/api/jobs")
def add_jobs(body: dict = Body(...)):
    # Files picked explicitly are retried even if an earlier attempt wasn't worth keeping;
    # "all candidates" leaves those out.
    paths = [(p, True) for p in body.get("paths") or []]
    for i in body.get("item_ids") or []:
        it = db.one("SELECT path FROM items WHERE id=?", (int(i),))
        if it:
            paths.append((it["path"], True))
    if body.get("all_candidates"):
        paths += [(r["path"], False) for r in db.q(
            "SELECT path FROM items WHERE source='scan' AND transcode_hint LIKE '%→%' AND status='ok' "
            "AND NOT EXISTS (SELECT 1 FROM transcode_skips k WHERE k.path=items.path AND k.size=items.size) ORDER BY size DESC")]
    n = sum(1 for p, explicit in paths
            if transcode.enqueue(p, int(body.get("priority", 0)), bool(body.get("force")), retry_skipped=explicit))
    return {"queued": n}


@app.post("/api/jobs/{job_id}/{action}")
def job_action(job_id: int, action: str):
    job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job:
        raise HTTPException(404)
    if action == "cancel":
        transcode.worker.cancel(job_id)
    elif action in ("pause", "resume"):
        if not transcode.worker.pause_job(job_id, action == "pause"):
            raise HTTPException(409, "job is not running")
    elif action == "retry":
        db.x("UPDATE jobs SET status='queued', progress=0, message=NULL, finished=NULL WHERE id=?", (job_id,))
    elif action == "force":
        db.x("UPDATE jobs SET status='queued', progress=0, message=NULL, finished=NULL, plan=? WHERE id=?",
             (db.jdump({"force": True}), job_id))
    elif action == "top":
        top = (db.one("SELECT MAX(priority) p FROM jobs WHERE status='queued'") or {}).get("p") or 0
        db.x("UPDATE jobs SET priority=? WHERE id=?", (top + 1, job_id))
    else:
        raise HTTPException(400, "unknown action")
    return {"ok": True}


@app.delete("/api/jobs")
def clear_jobs(status: str = "done,skipped,cancelled,failed"):
    st = status.split(",")
    db.x("DELETE FROM jobs WHERE status IN (%s)" % ",".join("?" * len(st)), st)
    return {"ok": True}


@app.post("/api/queue/{action}")
def queue_action(action: str):
    if action not in ("pause", "resume"):
        raise HTTPException(400)
    transcode.worker.paused = action == "pause"
    return {"paused": transcode.worker.paused}


# ---------------------------------------------------------------- imports
@app.get("/api/imports")
def list_imports(limit: int = 200):
    rows = db.q("SELECT * FROM imports ORDER BY updated DESC LIMIT ?", (limit,))
    for r in rows:
        r["items"] = [_item_out(i) for i in db.q("SELECT * FROM items WHERE import_id=? ORDER BY path", (r["id"],))]
    return rows


@app.post("/api/imports/{imp_id}/{action}")
def import_action(imp_id: int, action: str):
    row = db.one("SELECT * FROM imports WHERE id=?", (imp_id,))
    if not row:
        raise HTTPException(404)
    if action == "retry":
        db.x("UPDATE imports SET signature='', status='waiting', message='queued for retry' WHERE id=?", (imp_id,))
        _bg(watcher.process_entry, row["entry"], row["signature"] or "")
    elif action == "ignore":
        db.x("UPDATE imports SET status='ignored' WHERE id=?", (imp_id,))
    elif action == "forget":
        db.x("DELETE FROM items WHERE import_id=? AND status!='applied'", (imp_id,))
        db.x("DELETE FROM imports WHERE id=?", (imp_id,))
    else:
        raise HTTPException(400)
    return {"ok": True}


@app.post("/api/watcher/poll")
def poll_now():
    _bg(watcher.poll)
    return {"ok": True}


# ---------------------------------------------------------------- log & UI
@app.get("/api/log")
def get_log(limit: int = 300, level: str = ""):
    if level:
        return db.q("SELECT * FROM log WHERE level=? ORDER BY id DESC LIMIT ?", (level, limit))
    return db.q("SELECT * FROM log ORDER BY id DESC LIMIT ?", (limit,))


@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    db.log("error", "api", f"{request.url.path}: {exc}")
    return JSONResponse({"detail": str(exc)}, status_code=500)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))
