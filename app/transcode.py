"""Hardware detection, per-file transcode planning and the transcode queue worker."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time

from . import config, db, probe

CODEC_RANK = {"av1": 3, "hevc": 2, "vp9": 2, "h264": 1}
ENCODERS = {
    "nvenc": {"hevc": "hevc_nvenc", "h264": "h264_nvenc", "av1": "av1_nvenc"},
    "qsv": {"hevc": "hevc_qsv", "h264": "h264_qsv", "av1": "av1_qsv"},
    "vaapi": {"hevc": "hevc_vaapi", "h264": "h264_vaapi", "av1": "av1_vaapi"},
    "software": {"hevc": "libx265", "h264": "libx264", "av1": "libsvtav1"},
}
BASE_Q = {
    "software": {"hevc": 22, "h264": 20, "av1": 30},
    "nvenc": {"hevc": 26, "h264": 23, "av1": 30},
    "qsv": {"hevc": 24, "h264": 22, "av1": 26},
    "vaapi": {"hevc": 24, "h264": 22, "av1": 110},
}
BACKEND_ORDER = ["nvenc", "qsv", "vaapi", "software"]
BAD_SUBS = {"eia_608", "dvb_teletext", "bin_data", "timed_id3"}
LOSSLESS_AUDIO = {"truehd", "flac", "mlp", "alac"}

hw: dict = {"detected": False}
needs_jellyfin_refresh = {"flag": False}


# ================================================================ hardware detection
def _dri_vendor(dev: str) -> str:
    try:
        node = os.path.basename(dev)
        with open(f"/sys/class/drm/{node}/device/vendor") as f:
            return {"0x8086": "intel", "0x1002": "amd", "0x10de": "nvidia"}.get(f.read().strip(), "unknown")
    except OSError:
        return "unknown"


def _hw_init(backend: str, dev: str) -> list[str]:
    if backend == "vaapi":
        return ["-init_hw_device", f"vaapi=va:{dev}", "-filter_hw_device", "va"]
    if backend == "qsv":
        return ["-init_hw_device", f"vaapi=va:{dev}", "-init_hw_device", "qsv=qs@va", "-filter_hw_device", "qs"]
    if backend == "nvenc":
        return ["-init_hw_device", "cuda=cu:0"]
    return []


def _probe_encoder(backend: str, codec: str, depth: int, dev: str) -> bool:
    enc = ENCODERS[backend][codec]
    fmt = "p010" if depth == 10 else "nv12"
    if backend == "vaapi":
        vf = f"format={fmt},hwupload"
    elif backend == "qsv":
        vf = f"format={fmt},hwupload=extra_hw_frames=64,format=qsv"
    elif backend == "nvenc":
        vf = "format=p010le" if depth == 10 else "format=yuv420p"
    else:
        vf = "format=yuv420p10le" if depth == 10 else "format=yuv420p"
    cmd = [probe.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", *_hw_init(backend, dev),
           "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-frames:v", "15", "-vf", vf,
           "-c:v", enc, *_profile_args(backend, codec, depth), "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def _x265_hdr10plus() -> bool:
    """x265 only honours dhdr10-info when built with ENABLE_HDR10_PLUS; probe with a missing file."""
    try:
        r = subprocess.run([probe.FFMPEG, "-hide_banner", "-v", "info", "-f", "lavfi", "-i", "testsrc2=s=64x64",
                            "-frames:v", "1", "-c:v", "libx265", "-x265-params", "dhdr10-info=/nonexistent.json",
                            "-f", "null", "-"], capture_output=True, text=True, timeout=30)
        err = (r.stderr or "").lower()
        return "disabled" not in err and "unknown option" not in err
    except Exception:
        return False


def detect_hardware() -> dict:
    try:
        enc_list = subprocess.run([probe.FFMPEG, "-hide_banner", "-encoders"], capture_output=True,
                                  text=True, timeout=30).stdout
    except Exception as e:
        hw.clear()
        hw.update(detected=True, error=f"ffmpeg not usable: {e}", backends={}, best={})
        return hw
    dev = config.get("device_path") or "/dev/dri/renderD128"
    dri = sorted(f"/dev/dri/{n}" for n in os.listdir("/dev/dri")) if os.path.isdir("/dev/dri") else []
    if dev not in dri and dri:
        dev = next((d for d in dri if "renderD" in d), dev)
    devices = [{"path": d, "vendor": _dri_vendor(d)} for d in dri if "renderD" in d]
    has_nvidia = os.path.exists("/dev/nvidia0") or os.path.exists("/dev/nvidiactl")
    if has_nvidia:
        devices.append({"path": "/dev/nvidia0", "vendor": "nvidia"})
    vendor = _dri_vendor(dev) if os.path.exists(dev) else None

    candidates = []
    if has_nvidia:
        candidates.append("nvenc")
    if os.path.exists(dev):
        if vendor in ("intel", "unknown"):
            candidates.append("qsv")
        candidates.append("vaapi")
    candidates.append("software")

    backends = {}
    for b in candidates:
        caps = {}
        for codec, enc in ENCODERS[b].items():
            if not re.search(rf"\b{enc}\b", enc_list):
                continue
            ok8 = _probe_encoder(b, codec, 8, dev)
            ok10 = _probe_encoder(b, codec, 10, dev) if codec != "h264" and ok8 else False
            if ok8:
                caps[codec] = {"8bit": ok8, "10bit": ok10}
        if caps:
            backends[b] = caps
    hw.clear()
    hw.update(detected=True, ts=time.time(), device=dev, vendor=vendor, devices=devices,
              cpu_count=os.cpu_count(), backends=backends, x265_hdr10plus=_x265_hdr10plus(),
              tools={k: bool(v) for k, v in TOOLS.items()})
    hw["best"] = {c: next((b for b in BACKEND_ORDER if c in backends.get(b, {})), None)
                  for c in ("hevc", "av1", "h264")}
    db.log("info", "hardware", "encoders: " + (", ".join(f"{b}[{'/'.join(c)}]" for b, c in backends.items()) or "none"))
    return hw


# ================================================================ planning
TOOLS = {"vmaf": shutil.which("vmaf"), "hdr10plus_tool": shutil.which("hdr10plus_tool"),
         "mkvmerge": shutil.which("mkvmerge")}
# CRF used when no VMAF search runs (visually transparent for most film content)
DEFAULT_CRF = {"av1": {"uhd": 24, "hd": 23, "sd": 22}, "hevc": {"uhd": 20, "hd": 19, "sd": 18},
               "h264": {"uhd": 19, "hd": 18, "sd": 17}}
# VMAF search bounds. VMAF is not an HDR metric (it over-rates dark PQ scenes), so HDR titles get a
# lower ceiling: the search can make a title better than the ceiling, never worse.
CRF_RANGE = {"av1": (16, 34), "hevc": (14, 26), "h264": (12, 24)}
CRF_CEILING_HDR = {"av1": 28, "hevc": 22, "h264": 20}


def crf_range(codec: str, s: dict) -> tuple[int, int]:
    lo, hi = CRF_RANGE[codec]
    if s.get("hdr") or s.get("dovi"):
        hi = min(hi, CRF_CEILING_HDR[codec])
    return lo, hi


def target_codec() -> str:
    """The configured policy, for display."""
    return config.get("target_codec") or "auto"


def codec_for(s: dict) -> str:
    """Per-title codec. auto: HEVC where only HEVC keeps the dynamic HDR players understand
    (Dolby Vision → profile 8.1, HDR10+), AV1 (most efficient) for everything else."""
    t = config.get("target_codec")
    if t in ENCODERS["software"]:
        return t
    return "hevc" if (s.get("dovi") or s.get("hdr10plus")) else "av1"


def _hdr_kind(s: dict) -> str:
    parts = []
    if s.get("dovi"):
        parts.append(f"DV{s.get('dv_profile') or ''}")
    if s.get("hdr10plus"):
        parts.append("HDR10+")
    elif s.get("hdr"):
        parts.append("HLG" if s.get("color_transfer") == "arib-std-b67" else "HDR10")
    return "+".join(parts)


def assess(s: dict, force: bool = False) -> tuple[bool, str]:
    """Should this file be transcoded? Returns (yes/no, human reason)."""
    if not s or not s.get("vcodec"):
        return False, "no video stream"
    tgt = codec_for(s)
    src = s["vcodec"]
    res = f"{s['height']}p" if s.get("height") else "?"
    kind = _hdr_kind(s)
    label = f"{src} {res}{' ' + kind if kind else ''}"
    if s.get("mediaman") and not force:
        return False, f"{label} — already transcoded"
    if s.get("dovi"):
        if config.get("dv_policy") == "skip":
            return False, f"{label} — Dolby Vision skipped by policy"
        if s.get("dv_profile") == 5:
            return False, f"{label} — DV profile 5 has no HDR10 base layer; left untouched"
        if tgt == "h264":
            return False, f"{label} — H.264 can't carry Dolby Vision"
    if s.get("hdr10plus"):
        if tgt != "hevc":
            return False, f"{label} — HDR10+ only survives as HEVC; set codec to auto or hevc"
        if not hdr10plus_supported():
            return False, f"{label} — HDR10+ tooling unavailable; left untouched"
    if s.get("hdr"):
        if tgt == "h264":
            return False, f"{label} — H.264 target can't keep HDR"
        if config.get("hdr_policy") == "skip" and not force:
            return False, f"{label} — HDR skipped by policy"
    if force:
        return True, f"{label} → {tgt} (forced)"
    max_h = int(config.get("max_height") or 0)
    downscale = bool(max_h and s.get("height", 0) > max_h * 1.05)
    rank_s, rank_t = CODEC_RANK.get(src, 0), CODEC_RANK.get(tgt, 2)
    bpp = s.get("bpp") or 0
    if not downscale:
        if rank_s >= rank_t and not (bpp > 0.15):
            return False, f"{label} — already efficient"
        if rank_s >= 2 and 0 < bpp < 0.12:
            # an already-compressed HEVC/VP9 release: re-encoding costs quality for a small saving
            return False, f"{label} — already efficient"
        if rank_s >= 1 and 0 < bpp < 0.04:
            return False, f"{label} — already low bitrate"
    tgt_bpp = {"hevc": 0.06, "av1": 0.045, "h264": 0.085}[tgt] * (0.6 if s.get("height", 0) > 1800 else 1)
    est = 1 - (tgt_bpp / bpp) if bpp else 0
    est_txt = f" (~{int(est * 100)}% smaller)" if est > 0.05 else ""
    return True, f"{label} → {tgt}{' ' + str(max_h) + 'p' if downscale else ''}{est_txt}"


def _profile_args(backend: str, codec: str, depth: int) -> list[str]:
    if codec == "hevc":
        return ["-profile:v", "main10" if depth == 10 else "main"]
    if codec == "h264":
        return ["-profile:v", "high"]
    return []


def _tier(height: int) -> str:
    return "uhd" if height >= 1800 else ("sd" if height and height <= 576 else "hd")


def default_crf(codec: str, height: int) -> int:
    return DEFAULT_CRF[codec][_tier(height)] + int(config.get("quality_bias") or 0)


def _quality(backend: str, codec: str, height: int) -> int:
    if backend == "software":
        return default_crf(codec, height)
    q = BASE_Q[backend][codec]
    step = 5 if (backend == "vaapi" and codec == "av1") else 1
    if height >= 1800:
        q += 2 * step
    elif height and height <= 576:
        q -= 2 * step
    q += int(config.get("quality_bias") or 0) * step
    return q


def _software_args(codec: str, depth: int, q: int, s: dict, full: bool = True) -> list[str]:
    """CPU encoder settings. full=False drops the metadata-only bits (used for quality-search samples)."""
    hdr = bool(s.get("hdr"))
    dv = bool(s.get("dovi")) and full
    if codec == "av1":
        params = ["tune=0", "enable-variance-boost=1"]
        mc, fa = (s.get("cll") or [0, 0])[:2]
        if hdr and mc:
            params.append(f"content-light={mc},{fa}")
        return ["-c:v", "libsvtav1", "-preset", str(int(config.get("av1_preset") or 5)), "-crf", str(q),
                "-pix_fmt", "yuv420p10le", "-svtav1-params", ":".join(params),
                "-dolbyvision", "1" if dv else "0"]
    if codec == "hevc":
        # explicit pools: x265's automatic NUMA pool setup fails inside Docker (set_mempolicy is blocked),
        # which silently disables its thread pool and WPP — ~3x slower on a 24-thread CPU
        params = ["log-level=error", "repeat-headers=1", "aq-mode=3", f"pools={os.cpu_count() or 4}"]
        if hdr:
            params += ["hdr10=1", "hdr10-opt=1"]
        if dv:   # x265 requires VBV limits when writing Dolby Vision
            kbps = min(max(int((s.get("vbitrate") or 0) / 1000), 20000), 120000)
            params += [f"vbv-maxrate={kbps}", f"vbv-bufsize={kbps * 2}"]
        return ["-c:v", "libx265", "-preset", config.get("x265_preset") or "slow", "-crf", str(q),
                "-pix_fmt", "yuv420p10le", "-profile:v", "main10", "-x265-params", ":".join(params),
                "-dolbyvision", "1" if dv else "0"]
    return ["-c:v", "libx264", "-preset", "slow", "-crf", str(q), "-pix_fmt", "yuv420p", "-profile:v", "high"]


def _encoder_args(backend: str, codec: str, depth: int, q: int, s: dict | None = None) -> list[str]:
    enc = ENCODERS[backend][codec]
    if backend == "software":
        return _software_args(codec, depth, q, s or {})
    if backend == "nvenc":
        a = ["-c:v", enc, "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", str(q), "-b:v", "0",
             "-rc-lookahead", "32", "-spatial-aq", "1"]
    elif backend == "qsv":
        a = ["-c:v", enc, "-preset", "slow", "-global_quality", str(q)]
        if codec == "h264":
            a += ["-look_ahead_depth", "40"]
    else:  # vaapi
        a = ["-c:v", enc, "-rc_mode", "CQP", "-qp", str(q)]
    return a + _profile_args(backend, codec, depth)


def _scaled(s: dict, max_h: int) -> tuple[int, int] | None:
    if not max_h or s.get("height", 0) <= max_h * 1.05 or not s.get("width"):
        return None
    w = int(round(s["width"] * max_h / s["height"] / 2) * 2)
    return w, max_h


def _stream_args(s: dict) -> list[str]:
    a = ["-map", "0:V:0", "-disposition:v:0", "default"]
    for i, au in enumerate(s.get("audio", [])):
        a += ["-map", f"0:a:{i}"]
        lossy_ok = True
        if config.get("audio_policy") == "eac3_lossless":
            c = (au.get("codec") or "")
            prof = (au.get("profile") or "")
            if c in LOSSLESS_AUDIO or c.startswith("pcm_") or (c == "dts" and ("MA" in prof or "X" in prof)):
                lossy_ok = False
        if lossy_ok:
            a += [f"-c:a:{i}", "copy"]
        else:
            a += [f"-c:a:{i}", "eac3", f"-b:a:{i}", "640k", f"-ac:a:{i}", str(min(6, au.get("channels") or 6))]
    o = 0
    for i, su in enumerate(s.get("subs", [])):
        c = su.get("codec") or ""
        if c in BAD_SUBS:
            continue
        a += ["-map", f"0:s:{i}", f"-c:s:{o}", "srt" if c == "mov_text" else "copy"]
        o += 1
    a += ["-map", "0:t?", "-c:t", "copy", "-map_metadata", "0", "-map_chapters", "0"]
    return a


def _color_args(s: dict) -> list[str]:
    a = []
    for opt, key in (("-color_primaries", "color_primaries"), ("-color_trc", "color_transfer"),
                     ("-colorspace", "color_space")):
        v = s.get(key)
        if v and v not in ("unknown", "reserved"):
            a += [opt, v]
    return a


def build_command(src: str, dst: str, s: dict, backend: str, codec: str, mode: str, crf: int | None = None) -> list[str]:
    """mode: 'hw' = hardware decode+encode, 'upload' = software decode + hardware encode, 'sw' = CPU only."""
    dev = hw.get("device") or config.get("device_path")
    depth = 10 if (s.get("bit_depth", 8) >= 10 and codec in ("hevc", "av1")) else 8
    if codec in ("av1", "hevc") and (backend == "software" or mode == "sw"):
        depth = 10
    size = _scaled(s, int(config.get("max_height") or 0))
    eff_backend = "software" if mode == "sw" else backend
    q = crf if crf is not None else _quality(eff_backend, codec, (size or (0, s.get("height", 0)))[1])
    pre = [probe.FFMPEG, "-hide_banner", "-nostdin", "-y", "-v", "error", "-progress", "pipe:1", "-nostats"]
    inp: list[str] = []
    vf: list[str] = []
    fmt8, fmt10 = "nv12", "p010"
    if backend == "software" or mode == "sw":
        if size:
            vf.append(f"scale={size[0]}:{size[1]}:flags=lanczos")
    elif backend == "nvenc":
        if mode == "hw":
            inp = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
            if size or (depth == 8 and s.get("bit_depth", 8) > 8):
                sz = f"{size[0]}:{size[1]}" if size else "-1:-1"
                vf.append(f"scale_cuda={sz}:format={'p010le' if depth == 10 else 'nv12'}")
        else:
            if size:
                vf.append(f"scale={size[0]}:{size[1]}")
            vf.append("format=p010le" if depth == 10 else "format=yuv420p")
    elif backend == "vaapi":
        inp = _hw_init("vaapi", dev)
        fmt = fmt10 if depth == 10 else fmt8
        if mode == "hw":
            inp += ["-hwaccel", "vaapi", "-hwaccel_device", "va", "-hwaccel_output_format", "vaapi"]
            if size or (depth == 8 and s.get("bit_depth", 8) > 8):
                sz = f"w={size[0]}:h={size[1]}:" if size else ""
                vf.append(f"scale_vaapi={sz}format={fmt}")
        else:
            if size:
                vf.append(f"scale={size[0]}:{size[1]}")
            vf += [f"format={fmt}", "hwupload"]
    elif backend == "qsv":
        inp = _hw_init("qsv", dev)
        fmt = fmt10 if depth == 10 else fmt8
        if mode == "hw":
            inp += ["-hwaccel", "vaapi", "-hwaccel_device", "va", "-hwaccel_output_format", "vaapi"]
            if size or (depth == 8 and s.get("bit_depth", 8) > 8):
                sz = f"w={size[0]}:h={size[1]}:" if size else ""
                vf.append(f"scale_vaapi={sz}format={fmt}")
            vf += ["hwmap=derive_device=qsv", "format=qsv"]
        else:
            if size:
                vf.append(f"scale={size[0]}:{size[1]}")
            vf += [f"format={fmt}", "hwupload=extra_hw_frames=64", "format=qsv"]
    cmd = pre + inp + ["-i", src] + _stream_args(s)
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += _encoder_args(eff_backend, codec, depth, q, s) + _color_args(s)
    stamp = f"{codec}/{eff_backend}/q{q} {dt.date.today().isoformat()}"
    cmd += ["-fps_mode", "passthrough", "-metadata", f"MEDIAMAN_TRANSCODED={stamp}",
            "-max_muxing_queue_size", "9999", "-f", "matroska", dst]
    return cmd


def hdr10plus_supported() -> bool:
    """HDR10+ is extracted from the source and injected into the finished HEVC stream, then remuxed with
    mkvmerge (which also writes the Dolby Vision config record when the stream carries DV)."""
    return bool(TOOLS["hdr10plus_tool"] and TOOLS["mkvmerge"])


def vmaf_enabled() -> bool:
    return bool(TOOLS["vmaf"]) and float(config.get("vmaf_target") or 0) > 0


def make_plan(path: str, s: dict, force: bool = False, crf: int | None = None) -> dict:
    if not hw.get("detected"):
        detect_hardware()
    ok, reason = assess(s, force)
    plan = {"transcode": ok, "reason": reason}
    if not ok:
        return plan
    codec = codec_for(s)
    backends = hw.get("backends", {})
    pref = config.get("preferred_backend") or "software"
    need10 = s.get("bit_depth", 8) >= 10 and codec != "h264"
    keep_meta = bool(s.get("hdr") or s.get("dovi") or s.get("hdr10plus"))

    def supports(b, c):
        caps = backends.get(b, {}).get(c)
        return bool(caps and (caps["10bit"] if need10 else caps["8bit"]))

    backend = None
    if pref != "software" and not keep_meta:
        # GPU only for SDR: GPU encoders can't carry HDR10 mastering data, Dolby Vision or HDR10+
        order = ([pref] if pref in backends else []) + [b for b in BACKEND_ORDER if b != pref]
        backend = next((b for b in order if b != "software" and supports(b, codec)), None)
    if not backend:
        backend = "software" if supports("software", codec) else None
    if not backend:
        return {"transcode": False, "reason": f"no working {codec} encoder found"}
    base = os.path.join(os.path.dirname(path), "." + os.path.splitext(os.path.basename(path))[0])
    tmp = base + ".mediaman.mkv"
    if backend == "software" and crf is None:
        crf = default_crf(codec, s.get("height", 0))
    attempts = []
    if backend != "software":
        attempts.append({"label": f"{backend} decode+encode", "cmd": build_command(path, tmp, s, backend, codec, "hw")})
        attempts.append({"label": f"cpu decode, {backend} encode", "cmd": build_command(path, tmp, s, backend, codec, "upload")})
        if supports("software", codec):
            attempts.append({"label": "cpu only", "cmd": build_command(path, tmp, s, "software", codec, "sw")})
    else:
        attempts.append({"label": f"cpu ({ENCODERS['software'][codec]})",
                         "cmd": build_command(path, tmp, s, "software", codec, "sw", crf)})
    expect = {"hdr": bool(s.get("hdr")), "transfer": s.get("color_transfer"),
              "mastering": bool(s.get("mastering")), "dovi": bool(s.get("dovi")),
              "hdr10plus": bool(s.get("hdr10plus"))}
    plan.update(backend=backend, codec=codec, crf=crf if backend == "software" else None, tmp=tmp,
                workdir=base + ".mediaman-work", attempts=attempts, expect=expect,
                search=backend == "software" and vmaf_enabled() and codec in ("av1", "hevc"),
                hdr10plus_json=(base + ".mediaman-work/hdr10plus.json") if s.get("hdr10plus") else None,
                output=os.path.splitext(path)[0] + ".mkv")
    return plan


def hdr_problem(expect: dict, out: dict) -> str | None:
    """Refuse a result that lost HDR information the source had."""
    if expect.get("hdr") and out.get("color_transfer") != expect.get("transfer"):
        return f"HDR transfer lost ({expect.get('transfer')} → {out.get('color_transfer') or 'none'})"
    if expect.get("mastering") and not out.get("mastering"):
        return "HDR10 mastering-display metadata lost"
    if expect.get("dovi") and not out.get("dovi"):
        return "Dolby Vision metadata lost"
    if expect.get("hdr10plus") and not out.get("hdr10plus"):
        return "HDR10+ metadata lost"
    return None


# ================================================================ queue
def protected(path: str) -> bool:
    """Never transcode files in the downloads folder — qBittorrent is still seeding them."""
    dl = config.get("downloads_dir")
    if not dl:
        return False
    a, b = os.path.realpath(path), os.path.realpath(dl).rstrip("/") + "/"
    return a.startswith(b)


def enqueue(path: str, priority: int = 0, force: bool = False) -> int | None:
    if protected(path):
        db.log("warn", "transcode", f"refused to queue a file in the downloads folder: {path}")
        return None
    if db.one("SELECT id FROM jobs WHERE path=? AND status IN ('queued','running','paused')", (path,)):
        return None
    return db.x("INSERT INTO jobs(path, status, priority, plan, created) VALUES(?,?,?,?,?)",
                (path, "queued", priority, db.jdump({"force": force}), time.time()))


def in_window(now: dt.datetime | None = None) -> bool:
    w = (config.get("transcode_window") or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d\d)\s*-\s*(\d{1,2}):(\d\d)", w)
    if not m:
        return True
    now = now or dt.datetime.now()
    a = int(m[1]) * 60 + int(m[2])
    b = int(m[3]) * 60 + int(m[4])
    t = now.hour * 60 + now.minute
    return a <= t < b if a <= b else (t >= a or t < b)


class Worker:
    def __init__(self):
        self.procs: dict[int, asyncio.subprocess.Process] = {}
        self.cancelled: set[int] = set()
        self.paused = False
        self.tasks: dict[int, asyncio.Task] = {}

    async def loop(self):
        while True:
            try:
                await self.tick()
            except Exception as e:
                db.log("error", "transcode", f"worker: {e}")
            await asyncio.sleep(3)

    async def tick(self):
        for jid, t in list(self.tasks.items()):
            if t.done():
                self.tasks.pop(jid, None)
        if self.paused or not in_window():
            return
        slots = max(1, int(config.get("concurrent_jobs") or 1)) - len(self.tasks)
        if slots <= 0:
            return
        for job in db.q("SELECT * FROM jobs WHERE status='queued' ORDER BY priority DESC, id LIMIT ?", (slots,)):
            db.x("UPDATE jobs SET status='running', started=?, progress=0, message=NULL WHERE id=?",
                 (time.time(), job["id"]))
            self.tasks[job["id"]] = asyncio.create_task(self.run(job))

    def cancel(self, jid: int):
        self.cancelled.add(jid)
        p = self.procs.get(jid)
        if p and p.returncode is None:
            try:
                os.killpg(p.pid, signal.SIGCONT)
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        db.x("UPDATE jobs SET status='cancelled', finished=? WHERE id=? AND status IN ('queued','running','paused')",
             (time.time(), jid))

    def pause_job(self, jid: int, pause: bool):
        p = self.procs.get(jid)
        if not p or p.returncode is not None:
            return False
        os.killpg(p.pid, signal.SIGSTOP if pause else signal.SIGCONT)
        db.x("UPDATE jobs SET status=? WHERE id=?", ("paused" if pause else "running", jid))
        return True

    async def run(self, job: dict):
        jid, path = job["id"], job["path"]
        opts = db.jload(job["plan"]) or {}
        try:
            await self._run(jid, path, bool(opts.get("force")))
        except Exception as e:
            db.x("UPDATE jobs SET status='failed', message=?, finished=? WHERE id=?", (str(e)[:2000], time.time(), jid))
            db.log("error", "transcode", f"{path}: {e}")
        finally:
            self.procs.pop(jid, None)

    async def _run(self, jid: int, path: str, force: bool):
        if not os.path.exists(path):
            raise RuntimeError("file no longer exists")
        if protected(path):
            raise RuntimeError("file is in the downloads folder (still seeding) — not transcoding it")
        s = probe.summarize(await asyncio.to_thread(probe.ffprobe, path, False))
        plan = await asyncio.to_thread(make_plan, path, s, force)
        orig_size = os.path.getsize(path)
        db.x("UPDATE jobs SET plan=?, orig_size=? WHERE id=?",
             (db.jdump({k: v for k, v in plan.items()} | {"force": force}), orig_size, jid))
        if not plan["transcode"]:
            db.x("UPDATE jobs SET status='skipped', message=?, progress=1, finished=? WHERE id=?",
                 (plan["reason"], time.time(), jid))
            return
        workdir = plan["workdir"]
        os.makedirs(workdir, exist_ok=True)
        try:
            await self._encode_and_replace(jid, path, s, plan, force, orig_size)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    async def _encode_and_replace(self, jid: int, path: str, s: dict, plan: dict, force: bool, orig_size: int):
        if plan.get("hdr10plus_json"):
            db.x("UPDATE jobs SET message=? WHERE id=?", ("extracting HDR10+ metadata", jid))
            ok, err = await self._sh(jid, f"{shlex.quote(probe.FFMPEG)} -nostdin -v error -i {shlex.quote(path)} "
                                          f"-map 0:V:0 -c:v copy -bsf:v hevc_mp4toannexb -f hevc - | "
                                          f"hdr10plus_tool extract -o {shlex.quote(plan['hdr10plus_json'])} -")
            if not ok or not os.path.getsize(plan["hdr10plus_json"]):
                raise RuntimeError(f"HDR10+ metadata extraction failed: {err[-400:]}")
        if plan.get("search"):
            crf, score, tried = await self._crf_search(jid, path, s, plan)
            if jid in self.cancelled:
                return
            plan = await asyncio.to_thread(make_plan, path, s, force, crf)
            plan["vmaf"] = {"crf": crf, "score": score, "tried": tried, "target": float(config.get("vmaf_target"))}
            db.x("UPDATE jobs SET plan=? WHERE id=?", (db.jdump(plan | {"force": force}), jid))
        tmp = plan["tmp"]
        errors = []
        for att in plan["attempts"]:
            if jid in self.cancelled:
                break
            db.x("UPDATE jobs SET message=? WHERE id=?", (f"{plan['reason']} via {att['label']}", jid))
            ok, err = await self._ffmpeg(jid, att["cmd"], s.get("duration") or 0)
            if ok:
                break
            errors.append(f"{att['label']}: {err}")
            if os.path.exists(tmp):
                os.unlink(tmp)
            db.log("warn", "transcode", f"{os.path.basename(path)} — {att['label']} failed: {err[-300:]}")
        else:
            raise RuntimeError("all attempts failed: " + " | ".join(e[-400:] for e in errors))
        if jid in self.cancelled:
            if os.path.exists(tmp):
                os.unlink(tmp)
            return
        if plan.get("hdr10plus_json"):
            await self._inject_hdr10plus(jid, plan, s)
            if jid in self.cancelled:
                return
        # ---- verify the result before touching the original
        out = probe.summarize(await asyncio.to_thread(probe.ffprobe, tmp, False))
        new_size = os.path.getsize(tmp)
        dur_in, dur_out = s.get("duration") or 0, out.get("duration") or 0
        problem = None
        if not out.get("vcodec"):
            problem = "output has no video stream"
        elif dur_in and abs(dur_in - dur_out) > max(2.0, dur_in * 0.01):
            problem = f"duration mismatch ({dur_in:.0f}s vs {dur_out:.0f}s)"
        elif len(out.get("audio", [])) != len(s.get("audio", [])):
            problem = "audio track count changed"
        elif [a.get("channels") for a in out.get("audio", [])] != [a.get("channels") for a in s.get("audio", [])] \
                and config.get("audio_policy") == "copy":
            problem = "audio channel layout changed"
        elif len(out.get("subs", [])) != len([x for x in s.get("subs", []) if (x.get("codec") or "") not in BAD_SUBS]):
            problem = "subtitle track count changed"
        else:
            problem = hdr_problem(plan.get("expect") or {}, out)
        min_save = float(config.get("min_savings_pct") or 0) / 100
        if not problem and new_size > orig_size * (1 - min_save) and not force:
            os.unlink(tmp)
            db.x("UPDATE jobs SET status='skipped', progress=1, new_size=?, finished=?, message=? WHERE id=?",
                 (new_size, time.time(), f"result only {100 - new_size * 100 // max(orig_size, 1)}% smaller — kept original", jid))
            return
        if problem:
            os.unlink(tmp)
            raise RuntimeError("verification failed: " + problem)
        final = await asyncio.to_thread(self._replace, path, tmp, plan["output"])
        saved = 100 - new_size * 100 // max(orig_size, 1)
        q = plan.get("vmaf")
        qtxt = f", CRF {q['crf']} (VMAF {q['score']:.1f})" if q else (f", CRF {plan['crf']}" if plan.get("crf") else "")
        db.x("UPDATE jobs SET status='done', progress=1, new_size=?, finished=?, message=?, path=? WHERE id=?",
             (new_size, time.time(), f"{plan['reason'].split(' (~')[0]} via {plan['backend']}{qtxt} — saved {saved}%",
              final, jid))
        db.x("UPDATE items SET path=?, size=?, codec=?, transcode_hint=?, updated=? WHERE path=?",
             (final, new_size, plan["codec"], "transcoded", time.time(), path))
        db.log("info", "transcode", f"{os.path.basename(final)}: {orig_size >> 20} MB → {new_size >> 20} MB")
        needs_jellyfin_refresh["flag"] = True

    def _replace(self, path: str, tmp: str, final: str) -> str:
        if os.path.normpath(final) != os.path.normpath(path) and os.path.exists(final):
            final = os.path.splitext(path)[0] + " (mediaman).mkv"
        if config.get("keep_originals"):
            from .organizer import root_for
            root, _ = root_for(path)
            base = root or os.path.dirname(path)
            trash = os.path.join(base, ".mediaman-trash", os.path.relpath(path, base))
            os.makedirs(os.path.dirname(trash), exist_ok=True)
            os.rename(path, trash)
            os.replace(tmp, final)
        else:
            os.replace(tmp, final)
            if os.path.normpath(final) != os.path.normpath(path):
                os.unlink(path)
        return final

    async def _sh(self, jid: int, script: str) -> tuple[bool, str]:
        """Run a shell pipeline in its own process group (so cancel/pause reach every stage)."""
        proc = await asyncio.create_subprocess_shell("set -o pipefail 2>/dev/null; " + script,
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                     start_new_session=True, executable="/bin/bash")
        self.procs[jid] = proc
        try:
            out, err = await proc.communicate()
        except BaseException:
            if proc.returncode is None:
                os.killpg(proc.pid, signal.SIGKILL)
            raise
        if jid in self.cancelled:
            return False, "cancelled"
        return proc.returncode == 0, (err or out or b"").decode(errors="ignore")

    async def _inject_hdr10plus(self, jid: int, plan: dict, s: dict):
        tmp, wd, js = plan["tmp"], plan["workdir"], plan["hdr10plus_json"]
        enc, inj, out = (os.path.join(wd, x) for x in ("enc.hevc", "inj.hevc", "remux.mkv"))
        q = shlex.quote
        db.x("UPDATE jobs SET message=? WHERE id=?", ("injecting HDR10+ metadata", jid))
        fps = s.get("fps_rational") or "24000/1001"
        steps = [
            f"{q(probe.FFMPEG)} -nostdin -v error -y -i {q(tmp)} -map 0:V:0 -c copy -bsf:v hevc_mp4toannexb -f hevc {q(enc)}",
            f"hdr10plus_tool inject -i {q(enc)} -j {q(js)} -o {q(inj)}",
            f"rm -f {q(enc)}",
            f"mkvmerge -q -o {q(out)} --default-duration 0:{fps}fps {q(inj)} -D {q(tmp)}; rc=$?; [ $rc -le 1 ]",
            f"mv -f {q(out)} {q(tmp)}",
        ]
        for st in steps:
            ok, err = await self._sh(jid, st)
            if not ok:
                if jid in self.cancelled:
                    return
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise RuntimeError(f"HDR10+ injection failed at '{st.split()[0]}': {err[-400:]}")

    async def _crf_search(self, jid: int, path: str, s: dict, plan: dict) -> tuple[int, float, dict]:
        """Find the highest CRF whose sample encodes still reach the VMAF target (binary search)."""
        codec, wd = plan["codec"], plan["workdir"]
        target = float(config.get("vmaf_target") or 95)
        dur = s.get("duration") or 0
        n = 4 if dur > 600 else (2 if dur > 60 else 1)
        seg = 8 if dur > 60 else max(2, int(dur / 3))
        points = [dur * (i + 1) / (n + 1) for i in range(n)] if dur > 60 else [0]
        samples = []
        for i, t in enumerate(points):
            sp = os.path.join(wd, f"ref{i}.mkv")
            ok, err = await self._sh(jid, f"{shlex.quote(probe.FFMPEG)} -nostdin -v error -y -ss {t:.2f} "
                                          f"-i {shlex.quote(path)} -t {seg} -map 0:V:0 -c copy {shlex.quote(sp)}")
            if not ok:
                raise RuntimeError(f"sample extraction failed: {err[-300:]}")
            samples.append(sp)
        model = "vmaf_4k_v0.6.1" if s.get("height", 0) >= 1800 else "vmaf_v0.6.1"
        threads = os.cpu_count() or 4
        enc = _software_args(codec, 10, 0, s, full=False)
        tried: dict[int, float] = {}
        ff = shlex.quote(probe.FFMPEG)

        async def score(crf: int) -> float:
            if crf in tried:
                return tried[crf]
            args = list(enc)
            args[args.index("-crf") + 1] = str(crf)
            vals = []
            for i, sp in enumerate(samples):
                dist = os.path.join(wd, f"dist{i}.mkv")
                ok, err = await self._sh(jid, f"{ff} -nostdin -v error -y -i {shlex.quote(sp)} "
                                              f"{' '.join(shlex.quote(a) for a in args)} -fps_mode passthrough "
                                              f"{shlex.quote(dist)}")
                if not ok:
                    raise RuntimeError(f"sample encode failed at CRF {crf}: {err[-300:]}")
                r, d, js = (os.path.join(wd, x) for x in ("r.y4m", "d.y4m", "vmaf.json"))
                for fifo in (r, d):
                    if os.path.exists(fifo):
                        os.unlink(fifo)
                    os.mkfifo(fifo)
                ok, err = await self._sh(jid,
                    f"{ff} -nostdin -v error -i {shlex.quote(sp)} -map 0:V:0 -pix_fmt yuv420p10le -strict -1 "
                    f"-f yuv4mpegpipe -y {shlex.quote(r)} & "
                    f"{ff} -nostdin -v error -i {shlex.quote(dist)} -map 0:V:0 -pix_fmt yuv420p10le -strict -1 "
                    f"-f yuv4mpegpipe -y {shlex.quote(d)} & "
                    f"vmaf -q -r {shlex.quote(r)} -d {shlex.quote(d)} --threads {threads} "
                    f"--model version={model} --json -o {shlex.quote(js)}; rc=$?; kill $(jobs -p) 2>/dev/null; wait; exit $rc")
                if not ok:
                    raise RuntimeError(f"VMAF failed at CRF {crf}: {err[-300:]}")
                with open(js) as f:
                    vals.append(float(json.load(f)["pooled_metrics"]["vmaf"]["mean"]))
            # the weakest sample decides: dark/grainy scenes must also look right
            sc = min(sum(vals) / len(vals), min(vals) + 1.0)
            tried[crf] = round(sc, 2)
            db.x("UPDATE jobs SET message=? WHERE id=?",
                 (f"quality search: CRF {crf} → VMAF {sc:.1f} (target {target:g})", jid))
            return sc

        lo, hi = crf_range(codec, s)
        best = None
        while lo <= hi and jid not in self.cancelled:
            mid = (lo + hi) // 2
            if await score(mid) >= target:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        if best is None:
            best = crf_range(codec, s)[0]
        return best, tried.get(best, 0.0), {str(k): v for k, v in sorted(tried.items())}

    async def _ffmpeg(self, jid: int, cmd: list[str], duration: float) -> tuple[bool, str]:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, start_new_session=True)
        self.procs[jid] = proc
        err_buf: list[bytes] = []

        async def drain():
            async for line in proc.stderr:
                err_buf.append(line)
                del err_buf[:-40]
        dtask = asyncio.create_task(drain())
        cur: dict[str, str] = {}
        last = 0.0

        def num(v) -> float:
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0
        try:
            async for raw in proc.stdout:
                line = raw.decode(errors="ignore").strip()
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cur[k] = v
                if k == "progress" and time.time() - last > 2:
                    last = time.time()
                    t = num(cur.get("out_time_us") or cur.get("out_time_ms")) / 1e6
                    prog = min(0.999, t / duration) if duration else 0
                    speed = cur.get("speed", "").strip()
                    sp = num(speed.rstrip("x"))
                    eta = int((duration - t) / sp) if sp and duration else None
                    db.x("UPDATE jobs SET progress=?, fps=?, speed=?, eta=? WHERE id=?",
                         (prog, num(cur.get("fps")), speed, eta, jid))
        except BaseException:
            if proc.returncode is None:
                os.killpg(proc.pid, signal.SIGKILL)
            raise
        rc = await proc.wait()
        await dtask
        err = b"".join(err_buf).decode(errors="ignore").strip()
        if jid in self.cancelled:
            return False, "cancelled"
        return rc == 0, err or f"exit code {rc}"


worker = Worker()
