"""ffprobe wrapper with an on-disk cache keyed by path+size+mtime."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from . import db


def _bin(name: str) -> str:
    env = os.environ.get("MEDIAMAN_" + name.upper())
    if env:
        return env
    jf = f"/usr/lib/jellyfin-ffmpeg/{name}"
    if os.path.exists(jf):
        return jf
    return shutil.which(name) or name


FFMPEG = _bin("ffmpeg")
FFPROBE = _bin("ffprobe")

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}


def ffprobe(path: str, use_cache: bool = True) -> dict | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    if use_cache:
        row = db.one("SELECT size, mtime, data FROM probes WHERE path=?", (path,))
        if row and row["size"] == st.st_size and abs(row["mtime"] - st.st_mtime) < 1:
            cached = db.jload(row["data"])
            if cached and "_frame_side_data" in cached:
                return cached
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
            capture_output=True, timeout=120, check=True).stdout
        data = json.loads(out)
    except Exception:
        return None
    # HDR10+ and light levels live in per-frame side data: peek at the first few frames
    try:
        fr = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "V:0", "-read_intervals", "%+#3", "-show_frames",
             "-show_entries", "frame=side_data_list", "-print_format", "json", path],
            capture_output=True, timeout=120).stdout
        sds = []
        for f in json.loads(fr or "{}").get("frames", []):
            sds += f.get("side_data_list", []) or []
        data["_frame_side_data"] = sds
    except Exception:
        data["_frame_side_data"] = []
    db.x("INSERT OR REPLACE INTO probes(path,size,mtime,data) VALUES(?,?,?,?)",
         (path, st.st_size, st.st_mtime, json.dumps(data)))
    return data


def _num(v, default=None):
    try:
        if isinstance(v, str) and "/" in v:
            a, b = v.split("/")
            return float(a) / float(b) if float(b) else default
        return float(v)
    except (TypeError, ValueError):
        return default


def main_video(data: dict) -> dict | None:
    vids = [s for s in data.get("streams", []) if s.get("codec_type") == "video"
            and not s.get("disposition", {}).get("attached_pic")]
    return vids[0] if vids else None


def _frame_count(v: dict) -> int | None:
    n = v.get("nb_frames")
    if n and str(n).isdigit():
        return int(n)
    for k, val in (v.get("tags") or {}).items():
        if k.upper().startswith("NUMBER_OF_FRAMES") and str(val).isdigit():
            return int(val)
    return None


def _vtag_duration(v: dict) -> float:
    for k, val in (v.get("tags") or {}).items():
        if k.upper().startswith("DURATION"):
            try:
                h, m, sec = str(val).split(":")
                return int(h) * 3600 + int(m) * 60 + float(sec)
            except ValueError:
                return 0.0
    return 0.0


def summarize(data: dict | None) -> dict:
    """Reduce ffprobe JSON to the facts the planner and UI need."""
    if not data:
        return {}
    fmt = data.get("format", {})
    v = main_video(data) or {}
    tags = {k.lower(): val for k, val in (fmt.get("tags") or {}).items()}
    duration = _num(fmt.get("duration")) or _num(v.get("duration")) or 0.0
    size = int(fmt.get("size") or 0)
    fps = _num(v.get("avg_frame_rate")) or _num(v.get("r_frame_rate")) or 24.0
    if fps > 240 or fps <= 0:
        fps = 24.0
    audio = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    subs = [s for s in data.get("streams", []) if s.get("codec_type") == "subtitle"]

    vbit = _num(v.get("bit_rate"))
    if not vbit:
        vtags = v.get("tags") or {}
        for k, val in vtags.items():
            if k.upper().startswith("BPS"):
                vbit = _num(val)
                break
    if not vbit:
        total = _num(fmt.get("bit_rate")) or ((size * 8 / duration) if duration else 0)
        abits = sum(_num(a.get("bit_rate"), 0) or 0 for a in audio)
        vbit = max(total - abits, total * 0.6) if total else 0

    pix = v.get("pix_fmt") or ""
    depth = 10 if ("10" in pix or "12" in pix) else 8
    if v.get("bits_per_raw_sample") in ("10", "12"):
        depth = 10
    transfer = v.get("color_transfer") or ""
    side = " ".join(str(sd.get("side_data_type", "")) for sd in v.get("side_data_list", []) or [])
    dovi = "DOVI" in side or (v.get("codec_tag_string") or "").lower() in ("dvh1", "dvhe", "dav1")
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    sds = list(v.get("side_data_list", []) or []) + list(data.get("_frame_side_data", []) or [])
    types = {str(sd.get("side_data_type", "")) for sd in sds}
    dv_profile = next((int(sd["dv_profile"]) for sd in sds if "dv_profile" in sd), None)
    cll = next(((int(sd.get("max_content") or 0), int(sd.get("max_average") or 0)) for sd in sds
                if sd.get("side_data_type") == "Content light level metadata"), (0, 0))
    hdr10plus = any("2094-40" in t or "HDR10+" in t for t in types)
    mastering = "Mastering display metadata" in types
    return {
        "container": fmt.get("format_name", ""),
        "duration": duration,
        "size": size,
        "vcodec": v.get("codec_name", ""),
        "profile": v.get("profile", ""),
        "width": w, "height": h, "fps": round(fps, 3),
        "fps_rational": v.get("r_frame_rate") if (v.get("r_frame_rate") or "0/0") != "0/0" else None,
        # constant frame rate (needed for resumable chunked encodes) and exact frame count when the file has one
        "cfr": (_num(v.get("r_frame_rate")) or 0) > 0
               and abs((_num(v.get("r_frame_rate")) or 0) - (_num(v.get("avg_frame_rate")) or _num(v.get("r_frame_rate")) or 0)) < 0.01,
        "frames": _frame_count(v),
        "vduration": _num(v.get("duration")) or _vtag_duration(v) or duration,
        "vstart": max(0.0, (_num(v.get("start_time")) or 0.0) - (_num(fmt.get("start_time")) or 0.0)),
        "pix_fmt": pix, "bit_depth": depth,
        "vbitrate": int(vbit or 0),
        "bpp": (vbit / (w * h * fps)) if (vbit and w and h) else 0,
        "color_primaries": v.get("color_primaries"),
        "color_transfer": transfer,
        "color_space": v.get("color_space"),
        "hdr": transfer in HDR_TRANSFERS,
        "dovi": dovi or dv_profile is not None,
        "dv_profile": dv_profile,
        "hdr10plus": hdr10plus,
        "mastering": mastering,
        "cll": list(cll),
        "audio": [{"codec": a.get("codec_name"), "channels": a.get("channels"),
                   "profile": a.get("profile"), "lang": (a.get("tags") or {}).get("language")}
                  for a in audio],
        "subs": [{"codec": s.get("codec_name"), "lang": (s.get("tags") or {}).get("language")} for s in subs],
        "tags": tags,
        "mediaman": any(k.startswith("mediaman") for k in tags),
    }
