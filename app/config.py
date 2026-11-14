"""Settings persisted as JSON in the config directory (a TrueNAS host path / ixVolume)."""
from __future__ import annotations

import copy
import json
import os
import threading

CONFIG_DIR = os.environ.get("MEDIAMAN_CONFIG", "/config")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
SECRET_KEYS = {"jellyfin_api_key", "qbittorrent_password"}
RETIRED_KEYS = ("tmdb_api_key",)   # dropped on load: no longer used, and no longer masked
MASK = "••••••••"

DEFAULTS: dict = {
    # Library layout (paths as seen inside the container)
    # one Media mount holding library and downloads (hardlinks need both on the same mount); the setup
    # wizard picks the real folders inside it
    "movies_dir": "/media/Movies",
    "tv_dir": "/media/Shows",
    "movies_more_dirs": [],         # more Movies folders (other pools/datasets); files there stay where they are
    "tv_more_dirs": [],             # more TV folders
    "extra_scan_dirs": [],          # unsorted folders to scan; proposals move files into movies/tv
    # qBittorrent finished-downloads watcher
    "downloads_dir": "/media/Downloads",
    "watcher_enabled": True,
    "watcher_interval": 60,         # seconds between polls
    "watcher_settle_seconds": 120,  # files must be unchanged this long before import
    "import_mode": "copy",          # copy | hardlink | move  (copy keeps qBittorrent seeding)
    "auto_import_min_confidence": 0.75,
    "auto_transcode_imports": False,  # opt-in: rewriting every download automatically is a big step
    "min_video_size_mb": 50,        # smaller videos are treated as samples
    # Optional integrations
    "jellyfin_url": "",
    "jellyfin_api_key": "",
    "jellyfin_verify_tls": True,
    "identify_with_jellyfin": True,  # confirm titles/years and add TMDb IDs via Jellyfin's search
    "qbittorrent_url": "",
    "qbittorrent_username": "",
    "qbittorrent_password": "",
    # Naming
    "naming_include_ids": True,
    "naming_include_episode_title": False,
    "auto_apply_scan_min_confidence": 0.0,  # 0 = scan proposals always wait for approval
    # Transcoding
    "target_codec": "auto",         # auto (AV1, or HEVC for Dolby Vision / HDR10+) | av1 | hevc | h264
    "preferred_backend": "software",  # software (best quality/efficiency) | auto (GPU for SDR) | nvenc | qsv | vaapi
    "vmaf_target": 95.0,            # per-title CRF search target; 0 = fixed CRF
    "av1_preset": 5,                # SVT-AV1 speed preset (lower = slower/better)
    "x265_preset": "slow",
    "quality_bias": 0,              # added to the CRF when no VMAF search runs (-4 .. +4)
    "max_height": 0,                # 0 = keep resolution, e.g. 1080 to downscale 4K
    "hdr_policy": "transcode",      # transcode (keep HDR10/HLG metadata) | skip
    "dv_policy": "mel_only",        # mel_only (skip profile 7 FEL) | convert (all → profile 8.1) | skip
    "audio_policy": "copy",         # copy | eac3_lossless (convert TrueHD/DTS-HD/FLAC/PCM to EAC3 640k)
    "min_savings_pct": 15,          # discard results that don't save at least this much
    "concurrent_jobs": 1,
    "overlap_finishing": True,      # start the next file while the last one is past encoding (joining, metadata, mux)
    "queue_paused": False,          # Pause queue also freezes the running job; survives restarts
    "resume_queue_on_startup": True,  # False: the queue comes back paused after a restart
    "transcode_window": "",         # e.g. "01:00-07:00"; empty = any time
    "keep_originals": True,         # move replaced originals to <dir>/.mediaman-trash instead of deleting
    "encoder_priority": "low",      # low (nice 10, lowest best-effort I/O) | normal
    "device_path": "",              # empty: pick the GPU whose encoders work
    "setup_done": False,            # the first-run setup wizard has been completed or skipped
    "settings_version": 4,
}

# keys whose defaults changed in a settings_version bump (older saved values are replaced once)
MIGRATIONS = {2: ("target_codec", "preferred_backend", "hdr_policy", "quality_bias")}
# defaults that changed in settings_version 3: an existing install that never saved these keeps the old
# behaviour instead of silently switching (new installs get the new defaults)
PRESERVE = {3: {"keep_originals": False, "auto_transcode_imports": True, "dv_policy": "convert",
                "device_path": "/dev/dri/renderD128"},
            4: {"movies_dir": "/media/movies", "tv_dir": "/media/tv", "downloads_dir": "/downloads",
                "setup_done": True}}

_lock = threading.Lock()
_settings: dict | None = None


def load() -> dict:
    global _settings
    with _lock:
        if _settings is None:
            data = copy.deepcopy(DEFAULTS)
            saved: dict = {}
            try:
                with open(SETTINGS_FILE) as f:
                    saved = json.load(f)
                data.update(saved)
            except FileNotFoundError:
                pass
            except Exception as e:  # corrupt file: keep defaults, don't crash
                print(f"settings.json unreadable ({e}); using defaults")
            ver = int(saved.get("settings_version") or 1) if saved else DEFAULTS["settings_version"]
            migrated = False
            for v, keys in sorted(MIGRATIONS.items()):
                if ver < v:
                    for k in keys:
                        data[k] = DEFAULTS[k]
                    migrated = True
            if saved:
                for v, old in sorted(PRESERVE.items()):
                    if ver < v:
                        for k, val in old.items():
                            if k not in saved:
                                data[k] = val
                                migrated = True
            data["settings_version"] = DEFAULTS["settings_version"]
            for k in RETIRED_KEYS:
                if k in data:
                    del data[k]
                    migrated = True
            if migrated:
                try:
                    with open(SETTINGS_FILE + ".tmp", "w") as f:
                        json.dump(data, f, indent=2)
                    os.replace(SETTINGS_FILE + ".tmp", SETTINGS_FILE)
                except OSError:
                    pass
            # environment overrides for first boot
            for k in ("movies_dir", "tv_dir", "downloads_dir"):
                env = os.environ.get("MEDIAMAN_" + k.upper())
                if env and not os.path.exists(SETTINGS_FILE):
                    data[k] = env
            _settings = data
        return _settings


def get(key: str):
    return load().get(key, DEFAULTS.get(key))


def public() -> dict:
    s = dict(load())
    for k in SECRET_KEYS:
        if s.get(k):
            s[k] = MASK
    return s


def update(changes: dict) -> dict:
    s = load()
    with _lock:
        for k, v in changes.items():
            if k not in DEFAULTS:
                continue
            if k in SECRET_KEYS and v == MASK:
                continue
            default = DEFAULTS[k]
            if isinstance(default, bool):
                v = bool(v)
            elif isinstance(default, int) and not isinstance(default, bool):
                v = int(v)
            elif isinstance(default, float):
                v = float(v)
            elif isinstance(default, list):
                if isinstance(v, str):
                    v = [p.strip() for p in v.splitlines() if p.strip()]
                v = list(v)
            elif isinstance(default, str):
                v = str(v).strip()
            s[k] = v
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(s, f, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    return public()
