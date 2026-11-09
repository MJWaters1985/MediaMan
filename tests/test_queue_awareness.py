import os
import tempfile

os.environ.setdefault("MEDIAMAN_CONFIG", tempfile.mkdtemp())

import pytest  # noqa: E402

from app import config, db, main, organizer  # noqa: E402


def _item(tmp_path, name="Old Name.mkv"):
    src = tmp_path / "movies" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"x" * 10)
    dst = tmp_path / "movies" / "Film (2000)" / "Film (2000).mkv"
    config.update({"movies_dir": str(tmp_path / "movies"), "downloads_dir": str(tmp_path / "dl")})
    iid = db.x("INSERT INTO items(path,source,kind,status,proposed_path,size) VALUES(?,?,?,?,?,?)",
               (str(src), "scan", "movie", "proposed", str(dst), 10))
    return iid, str(src), str(dst)


@pytest.mark.parametrize("status", ["running", "paused"])
def test_no_rename_while_transcoding(tmp_path, status):
    iid, src, _ = _item(tmp_path)
    jid = db.x("INSERT INTO jobs(path,status) VALUES(?,?)", (src, status))
    with pytest.raises(ValueError, match="being transcoded"):
        organizer.apply_item(iid)
    assert os.path.exists(src)
    db.x("DELETE FROM jobs WHERE id=?", (jid,))


def test_rename_carries_queued_job(tmp_path):
    iid, src, dst = _item(tmp_path, "Queued.mkv")
    jid = db.x("INSERT INTO jobs(path,status) VALUES(?,?)", (src, "queued"))
    organizer.apply_item(iid)
    assert db.one("SELECT path FROM jobs WHERE id=?", (jid,))["path"] == dst
    assert main._item_out(db.one("SELECT * FROM items WHERE id=?", (iid,)))["job"]["status"] == "queued"
    db.x("DELETE FROM jobs WHERE id=?", (jid,))


def test_work_files_not_scanned(tmp_path):
    d = tmp_path / "lib" / "Film (2000)"
    (d / ".Film (2000).mediaman-work").mkdir(parents=True)
    for f in ("Film (2000).mkv", ".Film (2000).mediaman.mkv", ".Film (2000).mediaman-work/ref0.mkv"):
        (d / f).write_bytes(b"x")
    assert [os.path.basename(p) for p in organizer.walk_videos(str(tmp_path / "lib"))] == ["Film (2000).mkv"]


def test_opening_db_does_not_requeue_running_jobs():
    jid = db.x("INSERT INTO jobs(path,status) VALUES(?,?)", ("/x.mkv", "running"))
    db._conn = None                     # a second process opening the database
    db.conn()
    assert db.one("SELECT status FROM jobs WHERE id=?", (jid,))["status"] == "running"
    db.requeue_interrupted()            # what the server does at startup
    assert db.one("SELECT status FROM jobs WHERE id=?", (jid,))["status"] == "queued"
    db.x("DELETE FROM jobs WHERE id=?", (jid,))


def test_queue_paused_at_startup_when_resume_is_off():
    from app import config, transcode
    config.update({"queue_paused": False, "resume_queue_on_startup": True})
    transcode.startup_queue_policy()
    assert transcode.worker.paused is False                       # default: carries on
    config.update({"resume_queue_on_startup": False})
    transcode.startup_queue_policy()
    assert transcode.worker.paused is True                        # off: comes back paused
    config.update({"queue_paused": False, "resume_queue_on_startup": True})



def test_leftover_work_folders_are_removed(tmp_path):
    import os
    import time
    from app import db, transcode
    lib = tmp_path / "lib"
    keepers, orphans = [], []
    for name, active, old in (("Busy", True, True), ("Gone", False, True), ("Fresh", False, False)):
        d = lib / f"{name} (2000)"
        d.mkdir(parents=True)
        src = d / f"{name} (2000).mkv"
        src.write_bytes(b"x")
        wd = transcode.workdir_for(str(src))
        os.makedirs(wd)
        open(os.path.join(wd, "c00000.obu"), "wb").write(b"y" * 1000)
        if old:
            t = time.time() - 3 * 86400
            os.utime(os.path.join(wd, "c00000.obu"), (t, t))
        if active:
            db.x("INSERT INTO jobs(path, status, created) VALUES(?, 'queued', ?)", (str(src), time.time()))
        (orphans if (old and not active) else keepers).append(wd)
    n, freed = transcode.sweep_workdirs([str(lib)])
    assert n == 1 and freed == 1000
    assert all(not os.path.exists(w) for w in orphans) and all(os.path.isdir(w) for w in keepers)
    db.x("DELETE FROM jobs WHERE path LIKE ?", (str(lib) + "%",))


def test_skipped_job_removes_its_chunks(tmp_path, monkeypatch):
    import asyncio
    import os
    from app import db, probe, transcode
    src = tmp_path / "Film (2001).mkv"
    src.write_bytes(b"x")
    wd = transcode.workdir_for(str(src))
    os.makedirs(wd)
    open(os.path.join(wd, "c00000.hevc"), "wb").write(b"z" * 5000)
    monkeypatch.setattr(probe, "ffprobe", lambda *a, **k: {})
    monkeypatch.setattr(probe, "summarize", lambda d: {})
    monkeypatch.setattr(transcode, "make_plan", lambda p, s, f: {"transcode": False, "reason": "kept as is",
                                                                 "workdir": wd})
    jid = db.x("INSERT INTO jobs(path, status, created) VALUES(?, 'running', 0)", (str(src),))
    asyncio.run(transcode.worker._run(jid, str(src), False))
    assert db.one("SELECT status FROM jobs WHERE id=?", (jid,))["status"] == "skipped"
    assert not os.path.exists(wd)
