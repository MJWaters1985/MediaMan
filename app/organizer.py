"""Library scanning, proposals and applying renames/moves/copies."""
from __future__ import annotations

import errno
import os
import shutil
import threading
import time

from . import config, db, integrations, naming, probe, transcode
from .identify import ARTWORK, identify, in_extras, is_sample, is_video

scan_state = {"running": False, "total": 0, "done": 0, "current": "", "started": None, "finished": None}
_scan_lock = threading.Lock()
_apply_lock = threading.Lock()


# ------------------------------------------------------------------ helpers
def roots() -> list[tuple[str, str | None]]:
    out = []
    if config.get("movies_dir"):
        out.append((config.get("movies_dir"), "movies"))
    if config.get("tv_dir"):
        out.append((config.get("tv_dir"), "tv"))
    for d in config.get("extra_scan_dirs") or []:
        out.append((d, None))
    return out


def root_for(path: str) -> tuple[str | None, str | None]:
    best = (None, None)
    for r, k in roots():
        if path.startswith(r.rstrip("/") + "/") and len(r) > len(best[0] or ""):
            best = (r, k)
    return best


def walk_videos(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith((".", "@")) and d != "#recycle")
        for f in sorted(filenames):
            p = os.path.join(dirpath, f)
            if f.startswith(".") or not is_video(p):
                continue
            rel = os.path.relpath(p, root)
            if in_extras(rel) or is_sample(rel):
                continue
            yield p


def _status_for(path: str, proposed: str | None) -> str:
    if not proposed:
        return "unknown"
    if os.path.normpath(proposed) == os.path.normpath(path):
        return "ok"
    if os.path.exists(proposed):
        return "conflict"
    return "proposed"


def propose(info: dict, path: str) -> str | None:
    return naming.build_path(
        info, os.path.splitext(path)[1], config.get("movies_dir"), config.get("tv_dir"),
        include_ids=config.get("naming_include_ids"),
        include_episode_title=config.get("naming_include_episode_title"))


def refresh_item(path: str, source: str, root: str | None, root_kind: str | None,
                 import_id: int | None = None, lookup: bool = True) -> dict:
    """(Re)identify one file and store/refresh its proposal row."""
    existing = db.one("SELECT * FROM items WHERE path=?", (path,))
    data = probe.ffprobe(path)
    summ = probe.summarize(data)
    old_info = db.jload(existing["info"]) if existing else None
    if old_info and old_info.get("manual"):
        info = old_info
    else:
        info = identify(path, root, root_kind, summ.get("tags"))
        if lookup:
            integrations.enrich(info)
    proposed = propose(info, path)
    from .identify import score
    info["confidence"] = score(info, root_kind)
    status = _status_for(path, proposed)
    if existing and existing["status"] in ("ignored",):
        status = existing["status"]
    if source == "import" and status == "ok":
        status = "applied"
    hint = transcode.assess(summ)[1] if summ else "unreadable"
    st_size = os.path.getsize(path) if os.path.exists(path) else 0
    row = (path, source, import_id, info["type"], db.jdump(info), info["confidence"], proposed, status,
           None, st_size, summ.get("vcodec"), summ.get("height"), int(bool(summ.get("hdr"))), hint, time.time())
    if existing:
        db.x("""UPDATE items SET source=?, import_id=COALESCE(?, import_id), kind=?, info=?, confidence=?,
                proposed_path=?, status=?, message=?, size=?, codec=?, height=?, hdr=?, transcode_hint=?, updated=?
                WHERE path=?""", row[1:] + (path,))
    else:
        db.x("""INSERT INTO items(path, source, import_id, kind, info, confidence, proposed_path, status, message,
                size, codec, height, hdr, transcode_hint, updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", row)
    return db.one("SELECT * FROM items WHERE path=?", (path,))


def override_item(item_id: int, fields: dict) -> dict | None:
    """User correction from the UI: replace identification fields and recompute the proposal."""
    item = db.one("SELECT * FROM items WHERE id=?", (item_id,))
    if not item:
        return None
    info = db.jload(item["info"]) or {}
    for k in ("type", "title", "year", "season", "edition", "episode_title"):
        if k in fields:
            v = fields[k]
            if k in ("year", "season"):
                v = int(v) if str(v).strip().lstrip("-").isdigit() else None
            info[k] = v or (0 if k == "season" and v == 0 else v)
    if "episodes" in fields:
        eps = [int(e) for e in str(fields["episodes"]).replace("-", ",").split(",") if e.strip().isdigit()]
        info["episodes"] = eps
    if "tmdb" in fields:
        info.setdefault("ids", {})
        if fields["tmdb"]:
            info["ids"]["tmdb"] = str(fields["tmdb"]).strip()
        else:
            info["ids"].pop("tmdb", None)
    info.pop("library_match", None)
    if fields.get("lookup"):
        info["ids"].pop("tmdb", None) if info.get("ids") and "tmdb" not in fields else None
        integrations.enrich(info)
    info["manual"] = True
    info["confidence"] = 1.0
    proposed = propose(info, item["path"])
    status = _status_for(item["path"], proposed)
    if item["source"] == "import" and status == "ok":
        status = "applied"
    db.x("UPDATE items SET kind=?, info=?, confidence=1.0, proposed_path=?, status=?, message=NULL, updated=? WHERE id=?",
         (info["type"], db.jdump(info), proposed, status, time.time(), item_id))
    return db.one("SELECT * FROM items WHERE id=?", (item_id,))


# ------------------------------------------------------------------ file operations
def transfer(src: str, dst: str, mode: str) -> str:
    """copy | hardlink | move. Never touches permissions (ACL datasets refuse chmod)."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if mode == "hardlink":
        try:
            os.link(src, dst)
            return "hardlinked"
        except OSError:
            mode = "copy"
    if mode == "move":
        try:
            os.rename(src, dst)
            return "moved"
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
    tmp = os.path.join(os.path.dirname(dst), "." + os.path.basename(dst) + ".mediaman-part")
    try:
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            shutil.copyfileobj(fi, fo, 16 * 1024 * 1024)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    if mode == "move":
        os.unlink(src)
        return "moved"
    return "copied"


def _prune_empty(d: str, stop: str) -> None:
    stop = os.path.normpath(stop)
    while d and os.path.normpath(d) != stop and d.startswith(stop):
        try:
            os.rmdir(d)          # only succeeds when empty
        except OSError:
            return
        d = os.path.dirname(d)


def apply_item(item_id: int) -> dict:
    with _apply_lock:
        item = db.one("SELECT * FROM items WHERE id=?", (item_id,))
        if not item:
            raise ValueError("no such item")
        src, dst = item["path"], item["proposed_path"]
        if not dst:
            raise ValueError("no proposed destination — identify it first")
        if os.path.normpath(src) == os.path.normpath(dst):
            db.x("UPDATE items SET status=? WHERE id=?", ("applied" if item["source"] == "import" else "ok", item_id))
            return {"result": "already in place", "path": dst}
        if not os.path.exists(src):
            db.x("UPDATE items SET status='error', message='source file missing' WHERE id=?", (item_id,))
            raise ValueError("source file missing")
        if os.path.exists(dst):
            if os.path.getsize(dst) == os.path.getsize(src) and item["source"] == "import":
                db.x("UPDATE items SET status='applied', message='already in library' WHERE id=?", (item_id,))
                return {"result": "already in library", "path": dst}
            db.x("UPDATE items SET status='conflict', message='destination exists' WHERE id=?", (item_id,))
            raise ValueError("destination already exists")

        mode = config.get("import_mode") if item["source"] == "import" else "move"
        side = naming.sidecars(src)
        src_dir = os.path.dirname(src)
        # folder artwork travels with a movie when it is the only video in its folder
        art = []
        if item["kind"] == "movie" and item["source"] == "scan":
            vids = [n for n in os.listdir(src_dir) if is_video(n)]
            if len(vids) == 1:
                art = [n for n in os.listdir(src_dir) if ARTWORK.match(n)]
        try:
            how = transfer(src, dst, mode)
        except OSError as e:
            db.x("UPDATE items SET status='error', message=? WHERE id=?", (str(e), item_id))
            db.log("error", "organize", f"{src} -> {dst}: {e}")
            raise
        dst_stem = os.path.splitext(dst)[0]
        for s, suffix in side:
            target = dst_stem + suffix
            if not os.path.exists(target):
                try:
                    transfer(s, target, mode)
                except OSError as e:
                    db.log("warn", "organize", f"sidecar {s}: {e}")
        for n in art:
            target = os.path.join(os.path.dirname(dst), n)
            if not os.path.exists(target):
                try:
                    transfer(os.path.join(src_dir, n), target, "move")
                except OSError:
                    pass
        if item["source"] == "scan":
            root, _ = root_for(src)
            if root:
                _prune_empty(src_dir, root)
            # the row now describes the new location
            db.x("DELETE FROM items WHERE path=? AND id<>?", (dst, item_id))
            db.x("UPDATE items SET path=?, status='ok', message=?, updated=? WHERE id=?",
                 (dst, f"{how} from {src}", time.time(), item_id))
            db.x("UPDATE jobs SET path=? WHERE path=? AND status IN ('queued','paused')", (dst, src))
        else:
            db.x("UPDATE items SET status='applied', message=?, updated=? WHERE id=?",
                 (f"{how} to library", time.time(), item_id))
        db.log("info", "organize", f"{how}: {src} -> {dst}")
        return {"result": how, "path": dst}


# ------------------------------------------------------------------ scanning
def scan(lookup: bool = True) -> None:
    if not _scan_lock.acquire(blocking=False):
        return
    try:
        scan_state.update(running=True, total=0, done=0, current="collecting files", started=time.time(),
                          finished=None)
        files: list[tuple[str, str, str | None]] = []
        for root, kind in roots():
            if not os.path.isdir(root):
                db.log("warn", "scan", f"library folder not found: {root}")
                continue
            files += [(p, root, kind) for p in walk_videos(root)]
        scan_state["total"] = len(files)
        seen = set()
        for p, root, kind in files:
            scan_state["current"] = os.path.relpath(p, root)
            try:
                refresh_item(p, "scan", root, kind, lookup=lookup)
            except Exception as e:
                db.log("error", "scan", f"{p}: {e}")
            seen.add(p)
            scan_state["done"] += 1
        # forget scan rows for files that vanished
        for row in db.q("SELECT id, path FROM items WHERE source='scan'"):
            if row["path"] not in seen and not os.path.exists(row["path"]):
                db.x("DELETE FROM items WHERE id=?", (row["id"],))
        counts = db.q("SELECT status, COUNT(*) n FROM items WHERE source='scan' GROUP BY status")
        db.log("info", "scan", f"scanned {len(files)} files: " +
               ", ".join(f"{c['n']} {c['status']}" for c in counts))
        thr = float(config.get("auto_apply_scan_min_confidence") or 0)
        if thr > 0:
            for row in db.q("SELECT id FROM items WHERE source='scan' AND status='proposed' AND confidence>=?", (thr,)):
                try:
                    apply_item(row["id"])
                except Exception:
                    pass
    finally:
        scan_state.update(running=False, current="", finished=time.time())
        _scan_lock.release()
