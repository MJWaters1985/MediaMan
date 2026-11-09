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
