# Changelog

All notable changes to MediaMan. Versions follow [semantic versioning](https://semver.org/).

## Unreleased

- First-run setup wizard: folders (with the folder picker and a write check), Jellyfin and qBittorrent
  (with Test buttons), automation and safety settings, and an optional password. The downloads watcher
  waits until it's finished or skipped. Existing installs skip it.
- New installs expect one Media mount at `/media` holding the library and downloads (so imports can
  hardlink); existing installs keep their folder settings.
- Licensed under GPL-3.0-or-later; the README lists the tools bundled in the image with their licenses.
- GitHub Actions: tests run on every push; a version tag builds, tests and publishes the image to
  `ghcr.io`.

## 1.7.2 — 2026-11-09

- A job that turns out to be skipped (for example after a Dolby Vision setting change mid-film) deletes
  the work files an earlier run left behind.
- The daily clean-up removes transcode work folders that no waiting job will use and that haven't
  changed for 2 days.

## 1.7.1 — 2026-11-09

- The two confidence thresholds are plain-language choices: *Rename library files automatically* and
  *Import downloads automatically*.
- Fixed: "never import automatically" wasn't possible (a threshold of 0 was treated as 75%).

## 1.7.0 — 2026-11-09

- Encoder threads follow the CPU limit TrueNAS gives the app, not the host's core count. The AV1
  encoder's parallelism is capped to fit half the memory limit (it used up to 18 GB at 4K on 24 threads).
- Encoders run at low CPU and disk priority (*Encoder priority*, default *low*).
- Dolby Vision profile 7 files are identified as FEL or MEL. New *mel_only* policy leaves FEL films
  alone, because converting drops their full enhancement layer.
- Jellyfin is told which files changed instead of rescanning the whole library; MediaMan's paths are
  translated to Jellyfin's.
- With no GPU configured, the one whose encoders work is chosen at startup.
- New-install defaults: Keep originals on, transcoding imports off, Dolby Vision *mel_only*. Existing
  installs keep their settings.

## 1.6.0 — 2026-11-09

- More Movies and TV folders, for libraries spread over several pools or datasets. A title goes to the
  folder that already has it; a series is never split.
- Free-space checks before copying into a dataset and before a transcode.
- Keep originals works when a film sits on its own dataset.
- Hardlink fallbacks are logged with the reason (e.g. different datasets).
- Reliable log pruning and a daily clean-up of expired lookups and stale file records.

## 1.5.0 — 2026-11-09

- Folder picker (*Browse…*) for the folder settings, limited to the folders mounted into MediaMan.

## 1.4.0 — 2026-11-09

- Web UI password set from Settings, stored as a salted scrypt hash. `MEDIAMAN_PASSWORD` is no longer
  used.
- Changes requested by other websites (cross-site requests) are refused.
- *Resume transcoding after a restart* setting: when off, the queue comes back paused.

## 1.3.0 — 2026-11-09

- Title lookups go through Jellyfin's TMDb search, so MediaMan no longer needs its own TMDb key.

## 1.2.3 — 2026-11-09

- An encoder that stops producing frames for 10 minutes (an ffmpeg/SVT-AV1 deadlock) is stopped and
  the chunk retried.

## 1.2.2 — 2026-11-08

- Encoders killed by the system (usually out of memory) are reported as such instead of with their
  start-up banner.
- End-to-end check of chunked encodes added to the repository.

## 1.2.1 — 2026-11-08

- Dolby Vision output is rewritten as a true profile 8.1 with `dovi_tool`; the output check requires
  profile 8.

## 1.2.0 — 2026-11-08

- CPU encodes of constant-frame-rate video run in 2-minute chunks and resume after a restart.
- *Pause queue* also freezes the running encode, and the pause survives restarts.
- Source frames are counted instead of trusting statistics tags, which can be stale.

## 1.1.3 — 2026-11-08

- Only the server requeues interrupted jobs; other processes opening the database no longer restart
  running encodes.

## 1.1.2 — 2026-11-08

- Files aren't renamed while they're being transcoded; the Library shows each file's queue state.

## 1.1.1 — 2026-11-08

- Transcodes skipped for too little savings are remembered and not queued again.

## 1.1.0 — 2026-11-08

- First version in this repository: library scanning and Jellyfin-style renaming, automatic import of
  finished downloads, and a hardware-aware transcode queue (AV1 for SDR/HDR10, HEVC for Dolby Vision
  and HDR10+, VMAF quality targeting).
