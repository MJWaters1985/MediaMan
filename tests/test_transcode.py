import datetime as dt
import os
import tempfile

os.environ["MEDIAMAN_CONFIG"] = tempfile.mkdtemp()

from app import config, transcode  # noqa: E402


def S(**kw):
    base = {"vcodec": "h264", "width": 1920, "height": 1080, "fps": 24, "bit_depth": 8, "bpp": 0.16,
            "hdr": False, "dovi": False, "dv_profile": None, "hdr10plus": False, "mastering": False, "cll": [0, 0],
            "mediaman": False, "audio": [{"codec": "aac", "channels": 2}],
            "subs": [{"codec": "mov_text"}, {"codec": "eia_608"}, {"codec": "subrip"}], "duration": 60}
    base.update(kw)
    return base


UHD = dict(vcodec="hevc", width=3840, height=2160, bit_depth=10, bpp=0.44, hdr=True, mastering=True,
           color_transfer="smpte2084", color_primaries="bt2020", color_space="bt2020nc", cll=[1000, 400])


def test_defaults_match_remux_goals():
    assert config.get("target_codec") == "auto" and config.get("preferred_backend") == "software"
    assert config.get("hdr_policy") == "transcode" and config.get("max_height") == 0


def test_codec_per_title():
    assert transcode.codec_for(S()) == "av1"
    assert transcode.codec_for(S(**UHD)) == "av1"                         # HDR10 → AV1
    assert transcode.codec_for(S(**UHD, dovi=True, dv_profile=7)) == "hevc"  # DV → HEVC 8.1
    assert transcode.codec_for(S(**UHD, hdr10plus=True)) == "hevc"         # HDR10+ → HEVC


def test_assess_rules():
    assert transcode.assess(S())[0]
    assert transcode.assess(S(**UHD))[0]                                   # 88 Mb/s HDR10 remux
    assert transcode.assess(S(**UHD, dovi=True, dv_profile=7, dv_el="MEL"))[0]
    ok, why = transcode.assess(S(**UHD, dovi=True, dv_profile=7, dv_el="FEL"))   # default: FEL left alone
    assert not ok and "full enhancement layer" in why and "FEL" in why
    assert transcode.assess(S(**UHD, dovi=True, dv_profile=7, dv_el="FEL"), force=True)[0]
    assert not transcode.assess(S(**UHD, dovi=True, dv_profile=7))[0]      # layer type unknown: left alone
    assert transcode.assess(S(**UHD, dovi=True, dv_profile=8))[0]
    config.update({"dv_policy": "convert"})
    assert transcode.assess(S(**UHD, dovi=True, dv_profile=7, dv_el="FEL"))[0]
    config.update({"dv_policy": "mel_only"})
    assert not transcode.assess(S(**UHD, dovi=True, dv_profile=5))[0]      # no HDR10 base layer
    assert not transcode.assess(S(vcodec="hevc", bpp=0.08))[0]             # already efficient
    assert not transcode.assess(S(bpp=0.02))[0]
    assert not transcode.assess(S(mediaman=True))[0]
    config.update({"target_codec": "av1"})
    assert not transcode.assess(S(**UHD, hdr10plus=True))[0]               # AV1 would drop HDR10+
    config.update({"target_codec": "auto", "hdr_policy": "skip"})
    assert not transcode.assess(S(**UHD))[0]
    config.update({"hdr_policy": "transcode", "dv_policy": "skip"})
    assert not transcode.assess(S(**UHD, dovi=True, dv_profile=8))[0]
    config.update({"dv_policy": "mel_only"})


def test_software_args_keep_hdr():
    a = transcode._software_args("av1", 10, 30, S(**UHD))
    j = " ".join(a)
    assert "libsvtav1" in j and "yuv420p10le" in j and "content-light=1000,400" in j and "-dolbyvision 0" in j
    h = " ".join(transcode._software_args("hevc", 10, 20, S(**UHD, dovi=True, dv_profile=7, vbitrate=88_000_000)))
    assert "libx265" in h and "hdr10=1" in h and "vbv-maxrate=88000" in h and "-dolbyvision 1" in h and "main10" in h
    sample = " ".join(transcode._software_args("hevc", 10, 20, S(**UHD, dovi=True), full=False))
    assert "-dolbyvision 0" in sample and "vbv" not in sample


def test_command_streams_and_scaling():
    transcode.hw.update(detected=True, device="/dev/dri/renderD128")
    config.update({"max_height": 720})
    cmd = transcode.build_command("/in.mp4", "/out.mkv", S(), "qsv", "hevc", "hw")
    j = " ".join(cmd)
    assert "-c:s:0 srt" in j and "0:s:1" not in j and "-map 0:s:2 -c:s:1 copy" in j
    assert "scale_vaapi=w=1280:h=720:format=nv12,hwmap=derive_device=qsv,format=qsv" in j
    assert "hevc_qsv" in j and "-fps_mode passthrough" in j
    config.update({"max_height": 0})
    cmd = transcode.build_command("/in.mkv", "/out.mkv", S(**UHD), "software", "av1", "sw", 27)
    j = " ".join(cmd)
    assert "-crf 27" in j and "-color_trc smpte2084" in j and "scale" not in j


def test_hevc_gets_light_levels_explicitly():
    """Disc remuxes often keep MaxCLL/MaxFALL only in the container; x265 must be told them."""
    args = " ".join(transcode._software_args("hevc", 10, 20, S(**UHD)))
    assert "max-cll=1000,400" in args
    assert "max-cll" not in " ".join(transcode._software_args("hevc", 10, 20, S(**dict(UHD, cll=[0, 0]))))
    assert "max-cll" not in " ".join(transcode._software_args("hevc", 10, 20, S()))          # SDR
    assert "content-light=1000,400" in " ".join(transcode._software_args("av1", 10, 30, S(**UHD)))


def test_lost_light_levels_are_refused():
    exp = {"hdr": True, "transfer": "smpte2084", "mastering": True, "cll": True}
    good = {"color_transfer": "smpte2084", "mastering": True, "cll": [1000, 400]}
    assert transcode.hdr_problem(exp, good) is None
    assert "light levels" in transcode.hdr_problem(exp, dict(good, cll=[0, 0]))
    assert transcode.hdr_problem(dict(exp, cll=False), dict(good, cll=[0, 0])) is None      # source had none


def test_hdr_verification():
    exp = {"hdr": True, "transfer": "smpte2084", "mastering": True, "dovi": True, "hdr10plus": False}
    good = {"color_transfer": "smpte2084", "mastering": True, "dovi": True}
    assert transcode.hdr_problem(exp, good) is None
    assert "transfer" in transcode.hdr_problem(exp, dict(good, color_transfer="bt709"))
    assert "Dolby" in transcode.hdr_problem(exp, dict(good, dovi=False))
    assert "mastering" in transcode.hdr_problem(exp, dict(good, mastering=False))


def test_downloads_are_protected(tmp_path):
    dl = tmp_path / "dl"
    (dl / "x").mkdir(parents=True)
    config.update({"downloads_dir": str(dl)})
    assert transcode.protected(str(dl / "x" / "a.mkv"))
    assert not transcode.protected(str(tmp_path / "media" / "a.mkv"))
    assert transcode.enqueue(str(dl / "x" / "a.mkv")) is None


def test_window():
    config.update({"transcode_window": "23:00-06:00"})
    assert transcode.in_window(dt.datetime(2026, 1, 1, 2, 0))
    assert not transcode.in_window(dt.datetime(2026, 1, 1, 12, 0))
    config.update({"transcode_window": ""})
    assert transcode.in_window(dt.datetime(2026, 1, 1, 12, 0))


def test_settings_migration(tmp_path, monkeypatch):
    import json
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"target_codec": "hevc", "preferred_backend": "auto", "hdr_policy": "skip",
                             "movies_dir": "/x"}))
    monkeypatch.setattr(config, "SETTINGS_FILE", str(f))
    monkeypatch.setattr(config, "_settings", None)
    s = config.load()
    assert s["target_codec"] == "auto" and s["preferred_backend"] == "software" and s["hdr_policy"] == "transcode"
    assert s["movies_dir"] == "/x" and json.loads(f.read_text())["settings_version"] == 4


def test_hdr_crf_ceiling():
    assert transcode.crf_range("hevc", S(**UHD)) == (14, 22)
    assert transcode.crf_range("av1", S(**UHD)) == (16, 28)
    assert transcode.crf_range("av1", S()) == (16, 34)


def test_skipped_files_are_remembered(tmp_path):
    from app import db
    config.update({"downloads_dir": str(tmp_path / "dl")})
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"x" * 100)
    transcode.remember_skip(str(f), "result only 9% smaller — kept original")
    assert transcode.skipped_before(str(f))
    assert transcode.enqueue(str(f)) is None                          # auto / queue-all
    jid = transcode.enqueue(str(f), retry_skipped=True)               # picked explicitly
    assert jid
    db.x("DELETE FROM jobs WHERE id=?", (jid,))
    assert transcode.enqueue(str(f), force=True)
    db.x("DELETE FROM jobs")
    f.write_bytes(b"y" * 120)                                         # file replaced → candidate again
    assert transcode.skipped_before(str(f)) is None
    assert transcode.enqueue(str(f))
    db.x("DELETE FROM jobs")


def test_chunked_plan_rules():
    transcode.TOOLS["mkvmerge"] = "/usr/bin/mkvmerge"
    base = S(**UHD, cfr=True, vduration=7200, fps=23.976, fps_rational="24000/1001")
    assert transcode.chunkable(base, "software", "av1")
    assert transcode.chunkable(dict(base, dovi=True), "software", "hevc")
    assert not transcode.chunkable(dict(base, dovi=True), "software", "av1")   # AV1 DV stays one encode
    assert not transcode.chunkable(dict(base, cfr=False), "software", "av1")   # VFR
    assert not transcode.chunkable(base, "vaapi", "hevc")                      # GPU
    cmd = transcode.chunk_command("/in.mkv", "/w/c1.obu", base, "av1", 24, 2877, 2877)
    j = " ".join(cmd)
    assert "-ss 119.974021" in j and "-frames:v 2877" in j and "-f obu" in j and "-an" in j
    assert "-ss" not in " ".join(transcode.chunk_command("/in.mkv", "/w/c0.obu", base, "av1", 24, 0, 2877))


def test_queue_pause_is_saved():
    transcode.worker.paused = True
    assert config.get("queue_paused") is True
    transcode.worker.paused = False
    assert config.get("queue_paused") is False


def test_dolby_vision_becomes_profile_8():
    exp = {"hdr": True, "transfer": "smpte2084", "dovi": True, "dv_profile": 8}
    good = {"color_transfer": "smpte2084", "dovi": True, "dv_profile": 8}
    assert transcode.hdr_problem(exp, good) is None
    assert "profile is 7" in transcode.hdr_problem(exp, dict(good, dv_profile=7))
    saved = transcode.TOOLS["dovi_tool"]
    transcode.TOOLS["dovi_tool"] = None
    try:
        ok, why = transcode.assess(S(**UHD, dovi=True, dv_profile=7, dv_el="MEL"))
        assert not ok and "dovi_tool" in why
    finally:
        transcode.TOOLS["dovi_tool"] = saved


def test_stalled_encoder_is_stopped(tmp_path, monkeypatch):
    """An encoder that keeps printing progress with a frozen frame count (an ffmpeg/SVT-AV1 deadlock) is killed."""
    import asyncio
    import time
    fake = tmp_path / "fake-ffmpeg"
    fake.write_text("#!/bin/sh\nwhile true; do printf 'frame=42\\nout_time_us=1000000\\nprogress=continue\\n'; "
                    "sleep 0.2; done\n")
    fake.chmod(0o755)
    monkeypatch.setattr(transcode, "STALL_SECONDS", 2)
    w = transcode.Worker()
    t0 = time.monotonic()
    ok, err = asyncio.run(w._ffmpeg(999, [str(fake)], 100))
    assert not ok and err.startswith(transcode.STALLED)
    assert time.monotonic() - t0 < 10
    assert w.procs[999].returncode is not None


def test_progressing_encoder_is_left_alone(tmp_path, monkeypatch):
    import asyncio
    fake = tmp_path / "fake-ffmpeg"
    fake.write_text("#!/bin/sh\ni=0\nwhile [ $i -lt 20 ]; do i=$((i+1)); printf 'frame=%s\\nprogress=continue\\n' $i; "
                    "sleep 0.2; done\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setattr(transcode, "STALL_SECONDS", 2)
    ok, err = asyncio.run(transcode.Worker()._ffmpeg(998, [str(fake)], 100))
    assert ok, err


def test_new_defaults_dont_change_existing_installs(tmp_path, monkeypatch):
    """settings_version 3 changed four defaults; an older settings file that never stored them keeps the old ones."""
    import json
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"settings_version": 2, "movies_dir": "/x", "keep_originals": True}))
    monkeypatch.setattr(config, "SETTINGS_FILE", str(f))
    monkeypatch.setattr(config, "_settings", None)
    s = config.load()
    assert s["keep_originals"] is True                      # stored: untouched
    assert s["auto_transcode_imports"] is True and s["dv_policy"] == "convert"   # not stored: old behaviour
    assert s["device_path"] == "/dev/dri/renderD128"
    monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "new.json"))      # a new install
    monkeypatch.setattr(config, "_settings", None)
    s = config.load()
    assert s["keep_originals"] is True and s["auto_transcode_imports"] is False
    assert s["dv_policy"] == "mel_only" and s["device_path"] == "" and s["encoder_priority"] == "low"
    assert s["setup_done"] is False and s["movies_dir"] == "/media/Movies"
    monkeypatch.setattr(config, "_settings", None)


def test_container_limits_and_av1_level(monkeypatch):
    lim = {"cpus": 24, "memory": 30 * 1024 ** 3}
    monkeypatch.setattr(transcode, "limits", lambda: lim)
    k4 = S(**UHD)
    assert transcode.svt_level(k4) == 5                     # 24 threads, 15 GB budget: level 5 (~8 GB at 4K)
    lim.update(cpus=4)
    assert transcode.svt_level(k4) == 2                     # few CPUs: low level whatever the memory
    lim.update(cpus=24, memory=8 * 1024 ** 3)
    assert transcode.svt_level(k4) == 2                     # 4 GB budget at 4K
    assert transcode.svt_level(S()) >= 4                    # 1080p needs far less
    config.update({"concurrent_jobs": 2})
    try:
        assert transcode.svt_level(k4) == 1                 # two jobs share the budget
    finally:
        config.update({"concurrent_jobs": 1})
    assert "lp=" in " ".join(transcode._software_args("av1", 10, 30, k4))


def test_limits_read_the_cgroup(monkeypatch, tmp_path):
    import builtins
    files = {"/sys/fs/cgroup/cpu.max": "400000 100000\n", "/sys/fs/cgroup/memory.max": str(6 * 1024 ** 3) + "\n"}
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda p, *a, **k: __import__("io").StringIO(files[p]) if p in files
                        else real_open(p, *a, **k))
    lim = transcode.limits()
    assert lim["cpus"] <= 4 and lim["memory"] == 6 * 1024 ** 3


def test_encoders_run_at_low_priority():
    assert transcode._priority_prefix()[:3] == ["nice", "-n", "10"]
    config.update({"encoder_priority": "normal"})
    try:
        assert transcode._priority_prefix() == []
    finally:
        config.update({"encoder_priority": "low"})


def test_gpu_is_picked_by_working_encoders(monkeypatch):
    monkeypatch.setattr(transcode, "_render_nodes", lambda: ["/dev/dri/renderD128", "/dev/dri/renderD129"])
    works = {("hevc", "/dev/dri/renderD129"), ("h264", "/dev/dri/renderD129"), ("h264", "/dev/dri/renderD128")}
    monkeypatch.setattr(transcode, "_probe_encoder", lambda b, c, d, dev: (c, dev) in works)
    config.update({"device_path": ""})
    assert transcode.pick_device() == "/dev/dri/renderD129"     # the one that can encode HEVC
    config.update({"device_path": "/dev/dri/renderD128"})
    try:
        assert transcode.pick_device() == "/dev/dri/renderD128"  # an explicit choice wins
    finally:
        config.update({"device_path": ""})



def test_existing_installs_skip_the_setup_wizard(tmp_path, monkeypatch):
    import json
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"settings_version": 3, "movies_dir": "/data/Media/Movies"}))
    monkeypatch.setattr(config, "SETTINGS_FILE", str(f))
    monkeypatch.setattr(config, "_settings", None)
    s = config.load()
    assert s["setup_done"] is True and s["movies_dir"] == "/data/Media/Movies"
    assert s["tv_dir"] == "/media/tv" and s["downloads_dir"] == "/downloads"     # old defaults kept
    monkeypatch.setattr(config, "_settings", None)
