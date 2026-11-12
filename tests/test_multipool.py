"""Several pools/datasets: library folders, moves and links between datasets, free space, log housekeeping."""
import errno
import os
import tempfile

os.environ["MEDIAMAN_CONFIG"] = tempfile.mkdtemp()

import pytest  # noqa: E402

from app import config, db, naming, organizer, transcode  # noqa: E402


def test_titles_go_where_they_already_are(tmp_path):
    main, pool2 = str(tmp_path / "tv"), str(tmp_path / "tv2")
    os.makedirs(os.path.join(pool2, "Severance (2022)", "Season 01"))
    os.makedirs(main)
    ep = {"type": "episode", "title": "Severance", "year": 2022, "season": 2, "episodes": [1]}
    # an existing series keeps getting its episodes on the pool it's on
    assert naming.build_path(dict(ep), ".mkv", [], [main, pool2]).startswith(pool2 + "/Severance (2022)/Season 02/")
    # a new series goes to the main folder
    new = dict(ep, title="Andor", year=2022)
    assert naming.build_path(new, ".mkv", [], [main, pool2]).startswith(main + "/Andor")
    # a file scanned in the second folder stays there
    assert naming.build_path(dict(new), ".mkv", [], [main, pool2], prefer_root=pool2).startswith(pool2 + "/Andor")
    # one folder given as a plain string still works
    assert naming.build_path(dict(new), ".mkv", [], main).startswith(main + "/Andor")


def test_more_folders_are_scanned_as_their_kind(tmp_path):
    m1, m2 = str(tmp_path / "m1"), str(tmp_path / "m2")
    config.update({"movies_dir": m1, "movies_more_dirs": [m2, m1], "tv_more_dirs": []})
    assert organizer.library_dirs("movies") == [m1, m2]            # duplicates dropped, main first
    assert (m2, "movies") in organizer.roots()
    assert organizer.root_for(os.path.join(m2, "X (2000)", "X (2000).mkv")) == (m2, "movies")
    config.update({"movies_more_dirs": []})


def test_hardlink_between_datasets_is_explained(tmp_path, monkeypatch):
    src = tmp_path / "dl" / "a.mkv"
    src.parent.mkdir()
    src.write_bytes(b"x" * 1000)

    def no_link(a, b):
        raise OSError(errno.EXDEV, "Invalid cross-device link")
    monkeypatch.setattr(os, "link", no_link)
    logged = []
    monkeypatch.setattr(db, "log", lambda lvl, area, msg: logged.append((lvl, msg)))
    assert organizer.transfer(str(src), str(tmp_path / "lib" / "a.mkv"), "hardlink") == "copied"
    assert logged and logged[0][0] == "warn" and "different datasets or mounts" in logged[0][1]
    assert (tmp_path / "lib" / "a.mkv").read_bytes() == src.read_bytes()


def test_copy_refused_when_the_dataset_is_full(tmp_path, monkeypatch):
    src = tmp_path / "a.mkv"
    src.write_bytes(b"x" * 1000)
    monkeypatch.setattr(organizer, "free_bytes", lambda p: 500)
    with pytest.raises(OSError) as e:
        organizer.transfer(str(src), str(tmp_path / "lib" / "a.mkv"), "copy")
    assert e.value.errno == errno.ENOSPC and "not enough free space" in str(e.value)
    assert not (tmp_path / "lib" / "a.mkv").exists()
    assert not [p for p in (tmp_path / "lib").iterdir()]            # no partial file left behind


def test_transcode_space_check(tmp_path, monkeypatch):
    f = tmp_path / "Movie.mkv"
    f.write_bytes(b"x")
    s = {"bpp": 0.4, "height": 2160}
    plan = {"codec": "av1", "workdir": str(tmp_path / ".work")}
    size = 80 * 1024 ** 3
    out = transcode.expected_output(s, "av1", size)
    assert size * 0.15 <= out < size * 0.2                          # ~12 GB for an 80 GB 4K remux
    monkeypatch.setattr(organizer, "free_bytes", lambda p: 10 * 1024 ** 3)
    msg = transcode.space_problem(str(f), s, plan, size)
    assert msg and "not enough free space" in msg and "10.0 GB free" in msg
    monkeypatch.setattr(organizer, "free_bytes", lambda p: 100 * 1024 ** 3)
    assert transcode.space_problem(str(f), s, plan, size) is None


def test_trash_on_a_child_dataset(tmp_path, monkeypatch):
    """Keep originals: the library-level trash can be on another dataset than the file; fall back to its own."""
    lib = tmp_path / "movies"
    d = lib / "4K" / "Dune (2021)"
    d.mkdir(parents=True)
    src, tmp = d / "Dune (2021).mkv", d / ".Dune.tmp.mkv"
    src.write_bytes(b"original")
    tmp.write_bytes(b"new")
    config.update({"keep_originals": True, "movies_dir": str(lib)})
    real_rename = os.rename

    def rename(a, b):
        if "/movies/.mediaman-trash/" in str(b):          # crossing from the 4K dataset to the parent one
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(a, b)
    monkeypatch.setattr(os, "rename", rename)
    try:
        final = transcode.worker._replace(str(src), str(tmp), str(src))
    finally:
        config.update({"keep_originals": False})
    assert final == str(src) and src.read_bytes() == b"new"
    assert (d / ".mediaman-trash" / "Dune (2021).mkv").read_bytes() == b"original"


def test_log_is_bounded_and_housekeeping(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "LOG_KEEP", 50)
    for i in range(250):
        db.log("info", "test", f"line {i}")
    assert db.one("SELECT COUNT(*) n FROM log")["n"] <= 150
    db.x("INSERT OR REPLACE INTO tmdb_cache(q,data,ts) VALUES('old','[]',0)")
    db.x("INSERT OR REPLACE INTO probes(path,size,mtime,data) VALUES(?,1,1,'{}')", (str(tmp_path / "gone.mkv"),))
    r = db.housekeeping()
    assert r["lookups"] >= 1 and r["probes"] >= 1
    assert db.one("SELECT COUNT(*) n FROM log")["n"] <= 50


def test_copy_reports_progress(tmp_path, monkeypatch):
    """A long copy shows up in copy_progress() while it runs (size, bytes done), and disappears when done."""
    import builtins
    import threading
    import time as _t
    src = tmp_path / "dl" / "Film.mkv"
    src.parent.mkdir()
    src.write_bytes(os.urandom(4 * 1024 * 1024))
    dst = tmp_path / "lib" / "Film (2001)" / "Film (2001).mkv"
    monkeypatch.setattr(organizer, "COPY_CHUNK", 256 * 1024)
    gate = threading.Event()

    class SlowFile:
        def __init__(self, f):
            self.f = f
        def read(self, n):
            gate.wait(5)                       # the test lets each chunk through after looking
            gate.clear()
            return self.f.read(n)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            self.f.close()

    real_open = builtins.open
    monkeypatch.setattr(organizer, "open", lambda p, m="r", *a, **k: SlowFile(real_open(p, m)) if str(p) == str(src)
                        else real_open(p, m, *a, **k), raising=False)
    t = threading.Thread(target=organizer.transfer, args=(str(src), str(dst), "copy"))
    t.start()
    seen = []
    for _ in range(40):
        gate.set()
        _t.sleep(0.02)
        seen += [c for c in organizer.copy_progress() if c["dst"] == str(dst)]
        if not t.is_alive():
            break
    while t.is_alive():
        gate.set()
        _t.sleep(0.01)
    t.join()
    mid = [c for c in seen if 0 < c["done"] < c["total"]]
    assert mid and mid[0]["total"] == 4 * 1024 * 1024 and mid[0]["name"] == "Film (2001).mkv"
    assert mid[0]["mode"] == "copy" and mid[0]["src"] == str(src)
    assert organizer.copy_progress() == []                       # finished copies are removed
    assert dst.read_bytes() == src.read_bytes()
