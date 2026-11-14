"""Tiny SQLite layer (WAL, one shared connection guarded by a lock)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from . import config

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS probes (
    path TEXT PRIMARY KEY, size INTEGER, mtime REAL, data TEXT);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL DEFAULT 'scan',      -- scan | import
    import_id INTEGER,
    kind TEXT,                                -- movie | episode | unknown
    info TEXT,                                -- identification JSON
    confidence REAL DEFAULT 0,
    proposed_path TEXT,
    status TEXT,                              -- ok | proposed | conflict | ignored | applied | error
    message TEXT,
    size INTEGER, codec TEXT, height INTEGER, hdr INTEGER DEFAULT 0,
    transcode_hint TEXT,
    updated REAL);
CREATE INDEX IF NOT EXISTS items_status ON items(status);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',    -- queued | running | paused | done | skipped | failed | cancelled
    priority INTEGER DEFAULT 0,
    progress REAL DEFAULT 0, fps REAL, speed TEXT, eta INTEGER,
    plan TEXT, message TEXT,
    orig_size INTEGER, new_size INTEGER,
    created REAL, started REAL, finished REAL);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry TEXT UNIQUE NOT NULL,
    signature TEXT,
    status TEXT,                              -- waiting | imported | review | failed | ignored | empty
    message TEXT,
    created REAL, updated REAL);
-- files whose transcode was skipped for too little savings; ignored again only while size+mtime match
CREATE TABLE IF NOT EXISTS transcode_skips (
    path TEXT PRIMARY KEY, size INTEGER, mtime REAL, reason TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS tmdb_cache (q TEXT PRIMARY KEY, data TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, level TEXT, area TEXT, msg TEXT);
"""


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(config.CONFIG_DIR, exist_ok=True)
        _conn = sqlite3.connect(os.path.join(config.CONFIG_DIR, "mediaman.db"),
                                check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(jobs)")}
        if "steps" not in cols:     # 1.8.5: seconds spent in each step, as JSON
            _conn.execute("ALTER TABLE jobs ADD COLUMN steps TEXT")
    return _conn


def requeue_interrupted():
    """Jobs left running/paused by a previous process go back to the queue. Only the server calls this at
    startup: any other process opening the database (a CLI check, a test) must not touch live jobs."""
    x("UPDATE jobs SET status='queued', progress=0 WHERE status IN ('running','paused')")


def q(sql: str, args=()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, args=()) -> dict | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def x(sql: str, args=()) -> int:
    with _lock:
        cur = conn().execute(sql, args)
        return cur.lastrowid


LOG_KEEP = 5000          # log rows kept in the database (the Log tab)
_log_count = 0


def log(level: str, area: str, msg: str) -> None:
    global _log_count
    # one line per event on stdout too (TrueNAS's app logs). Docker on TrueNAS doesn't rotate container logs,
    # so nothing chatty goes here: ffmpeg's output stays in memory and only a summary is logged.
    print(f"[{level}] {area}: {msg[:2000]}", flush=True)
    x("INSERT INTO log(ts,level,area,msg) VALUES(?,?,?,?)", (time.time(), level, area, msg[:4000]))
    _log_count += 1
    if _log_count % 100 == 0:
        x("DELETE FROM log WHERE id <= (SELECT MAX(id) - ? FROM log)", (LOG_KEEP,))


def housekeeping() -> dict:
    """Daily: keep the log bounded, drop expired lookups and cached probes of files that no longer exist."""
    out = {}
    with _lock:
        c = conn()
        out["log"] = c.execute("DELETE FROM log WHERE id <= (SELECT MAX(id) - ? FROM log)", (LOG_KEEP,)).rowcount
        out["lookups"] = c.execute("DELETE FROM tmdb_cache WHERE ts < ?", (time.time() - 30 * 86400,)).rowcount
        gone = [r[0] for r in c.execute("SELECT path FROM probes").fetchall() if not os.path.exists(r[0])]
        for p in gone:
            c.execute("DELETE FROM probes WHERE path=?", (p,))
        out["probes"] = len(gone)
        c.execute("PRAGMA optimize")
    return out


def jdump(o) -> str:
    return json.dumps(o, default=str)


def jload(s):
    return json.loads(s) if s else None
