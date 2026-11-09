"""Watch qBittorrent's finished folder and import new downloads into the Jellyfin library."""
from __future__ import annotations

import asyncio
import os
import time

from . import config, db, integrations, organizer, transcode
from .identify import in_extras, is_sample, is_video

state = {"last_poll": None, "error": None}
_stable: dict[str, tuple[str, float]] = {}   # entry -> (signature, first seen with that signature)


def _signature(path: str) -> tuple[str, float, bool]:
    """(signature, newest mtime, still downloading?)"""
    total, newest, count, partial = 0, 0.0, 0, False
    if os.path.isfile(path):
        st = os.stat(path)
        return f"{st.st_size}:{int(st.st_mtime)}:1", st.st_mtime, path.endswith((".!qB", ".part"))
    for dp, _, fns in os.walk(path):
        for f in fns:
            if f.endswith((".!qB", ".part", ".parts")):
                partial = True
            try:
                st = os.stat(os.path.join(dp, f))
            except OSError:
                continue
            total += st.st_size
            newest = max(newest, st.st_mtime)
            count += 1
    return f"{total}:{int(newest)}:{count}", newest, partial


def _videos(entry_path: str) -> list[str]:
    if os.path.isfile(entry_path):
        return [entry_path] if is_video(entry_path) else []
    vids = []
    for dp, dns, fns in os.walk(entry_path):
        dns[:] = [d for d in dns if not d.startswith(".")]
        for f in fns:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, entry_path)
            if is_video(p) and not is_sample(rel) and not in_extras(rel):
                vids.append(p)
    min_size = int(config.get("min_video_size_mb") or 0) * 1024 * 1024
    big = [v for v in vids if os.path.getsize(v) >= min_size]
    return sorted(big if big else vids[:1])


def process_entry(name: str, signature: str) -> None:
    dl = config.get("downloads_dir")
    entry_path = os.path.join(dl, name)
    row = db.one("SELECT * FROM imports WHERE entry=?", (name,))
    now = time.time()
    if row:
        db.x("UPDATE imports SET signature=?, status='processing', message=NULL, updated=? WHERE id=?",
             (signature, now, row["id"]))
        imp_id = row["id"]
    else:
        imp_id = db.x("INSERT INTO imports(entry, signature, status, created, updated) VALUES(?,?,?,?,?)",
                      (name, signature, "processing", now, now))
    vids = _videos(entry_path)
    if not vids:
        db.x("UPDATE imports SET status='empty', message='no video files', updated=? WHERE id=?", (now, imp_id))
        return
    thr = config.get("auto_import_min_confidence")
    thr = 0.75 if thr is None else float(thr)      # above 1 (the "never" choice): every download waits for review
    imported, review, failed = 0, 0, 0
    for v in vids:
        existing = db.one("SELECT * FROM items WHERE path=?", (v,))
        if existing and existing["status"] in ("applied", "ignored"):
            imported += existing["status"] == "applied"
            continue
        try:
            item = organizer.refresh_item(v, "import", dl, None, import_id=imp_id)
        except Exception as e:
            db.log("error", "import", f"{v}: {e}")
            failed += 1
            continue
        if item["status"] == "proposed" and item["confidence"] >= thr:
            try:
                res = organizer.apply_item(item["id"])
                imported += 1
                if config.get("auto_transcode_imports"):
                    transcode.enqueue(res["path"])
            except Exception as e:
                db.log("error", "import", f"{os.path.basename(v)}: {e}")
                failed += 1
        elif item["status"] == "applied":
            imported += 1
        else:
            review += 1
    status = "review" if review else ("failed" if failed and not imported else "imported")
    msg = f"{imported} imported" + (f", {review} need review" if review else "") + (f", {failed} failed" if failed else "")
    db.x("UPDATE imports SET status=?, message=?, updated=? WHERE id=?", (status, msg, time.time(), imp_id))
    db.log("info", "import", f"{name}: {msg}")


def poll() -> None:
    dl = config.get("downloads_dir")
    if not dl or not os.path.isdir(dl):
        state["error"] = f"downloads folder not found: {dl}"
        return
    state["error"] = None
    settle = int(config.get("watcher_settle_seconds") or 0)
    completed = integrations.qbit_completed_names()
    now = time.time()
    for name in sorted(os.listdir(dl)):
        if name.startswith((".", "@")) or name == "#recycle":
            continue
        p = os.path.join(dl, name)
        try:
            sig, newest, partial = _signature(p)
        except OSError:
            continue
        row = db.one("SELECT status, signature FROM imports WHERE entry=?", (name,))
        if row and row["signature"] == sig and row["status"] not in ("waiting",):
            continue      # already handled this exact content
        if os.path.isfile(p) and not is_video(p):
            continue
        waiting_reason = None
        if partial:
            waiting_reason = "still downloading"
        elif completed is not None and name not in completed:
            waiting_reason = "qBittorrent reports incomplete"
        else:
            prev = _stable.get(name)
            if not prev or prev[0] != sig:
                _stable[name] = (sig, now)
            if now - max(newest, _stable[name][1]) < settle:
                waiting_reason = "waiting for files to settle"
        if waiting_reason:
            if row:
                db.x("UPDATE imports SET status='waiting', message=?, updated=? WHERE entry=?", (waiting_reason, now, name))
            else:
                db.x("INSERT INTO imports(entry, signature, status, message, created, updated) VALUES(?,?,?,?,?,?)",
                     (name, "", "waiting", waiting_reason, now, now))
            continue
        _stable.pop(name, None)
        process_entry(name, sig)
    # entries removed from the downloads folder (torrent deleted) disappear from the waiting list
    for row in db.q("SELECT id, entry FROM imports WHERE status='waiting'"):
        if not os.path.exists(os.path.join(dl, row["entry"])):
            db.x("DELETE FROM imports WHERE id=?", (row["id"],))


async def loop():
    last_refresh = 0.0
    while True:
        # nothing is imported until the setup wizard is done (its folders and rules may not be right yet)
        if config.get("watcher_enabled") and config.get("setup_done"):
            try:
                await asyncio.to_thread(poll)
            except Exception as e:
                state["error"] = str(e)
                db.log("error", "watcher", str(e))
            state["last_poll"] = time.time()
        # batch Jellyfin updates: at most one call a minute, listing the files that changed
        if integrations.jellyfin_pending() and time.time() - last_refresh > 60:
            last_refresh = time.time()
            await asyncio.to_thread(integrations.jellyfin_flush)
        await asyncio.sleep(max(10, int(config.get("watcher_interval") or 60)))
