import os
import tempfile

os.environ["MEDIAMAN_CONFIG"] = tempfile.mkdtemp()

import pytest  # noqa: E402

from app import fsbrowse  # noqa: E402


def _mountinfo(tmp_path, mounts):
    f = tmp_path / "mountinfo"
    f.write_text("".join(f"{i} 1 0:1 / {m.replace(' ', chr(92) + '040')} rw - fs src rw\n" for i, m in enumerate(mounts)))
    return str(f)


def test_roots_are_the_mounted_media_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(fsbrowse, "SYSTEM", tuple(s for s in fsbrowse.SYSTEM if s != "/tmp"))  # pytest's tmp_path
    media = tmp_path / "Share Media"           # a space, escaped as \040 in mountinfo
    (media / "Movies").mkdir(parents=True)
    other = tmp_path / "extra"
    other.mkdir()
    mi = _mountinfo(tmp_path, ["/", "/proc", "/dev/shm", "/etc/hosts", fsbrowse.config.CONFIG_DIR,
                               str(media), str(media / "Movies"), str(other)])
    # system mounts, the config folder and a mount nested inside another are left out
    assert fsbrowse.roots(mi) == sorted([str(media), str(other)])


def test_listing_stays_inside_the_mounts(tmp_path, monkeypatch):
    media = tmp_path / "media"
    for d in ("Movies", "Shows", ".mediaman-trash", "Downloads"):
        (media / d).mkdir(parents=True)
    (media / "file.mkv").write_text("x")
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, media / "escape")
    monkeypatch.setattr(fsbrowse, "roots", lambda *a: [str(media)])

    top = fsbrowse.listing(None)
    assert [d["path"] for d in top["dirs"]] == [str(media)] and top["path"] is None
    d = fsbrowse.listing(str(media))
    assert [x["name"] for x in d["dirs"]] == ["Downloads", "escape", "Movies", "Shows"]   # no hidden, no files
    assert d["parent"] is None and d["writable"] is True                                  # can't go above a mount
    assert fsbrowse.listing(str(media / "Movies"))["parent"] == str(media)
    for bad in (str(outside), str(media / "escape"), str(media / ".." / "outside"), "/etc"):
        with pytest.raises(ValueError):
            fsbrowse.listing(bad)
