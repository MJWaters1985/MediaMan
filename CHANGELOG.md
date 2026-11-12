# Changelog

All notable changes to MediaMan. Versions follow [semantic versioning](https://semver.org/).

## 1.8.4 — 2026-11-12

- Jellyfin updates are no longer lost when Jellyfin can't be reached (a restart, an update, a certificate
  problem): the changed files are kept, also across a MediaMan restart, and sent once it's back, retrying
  after 1 minute and then less often (up to every 30 minutes). An outage logs one warning, and a note when
  it has caught up.

## 1.8.3 — 2026-11-12

- Fixed: some Dolby Vision profile 7 discs (with the enhancement layer stored as Matroska block additions)
  failed on their first chunk, because ffmpeg couldn't hand their metadata to x265. The Dolby Vision
  metadata is now read from the original with `dovi_tool`, converted to profile 8.1 and added to the new
  video frame for frame; RPUs for undecodable leading frames are dropped, and any other mismatch fails the
  job with the original kept. Bitrate limits are unchanged. A Dolby Vision job in progress when this is
  installed starts over.
- The transcode queue shows which step a job is on (for example *Step 2 of 7 · Counting frames*), that
  step's own progress (from how much of the file has been read, or the quality-search test) and time, and the
  full list of steps. Steps that read a big file (joining the chunks, adding metadata, writing the final
  file, the final check) also show how much has been read, the read speed and the time left.
- Error messages lead with the tool's actual error line instead of a cut-off tail.
- Fixed: calls to a Jellyfin server that doesn't send its intermediate certificate (common when Jellyfin
  serves a .pfx) failed with *unable to get local issuer certificate*, so lookups and library updates
  stopped. MediaMan now fetches the missing certificates from the address in the server's certificate, as
  browsers do, and keeps them in /config. The chain must still end at a trusted root. A check that still
  fails is explained in plain words.
- qBittorrent connection blips (a slow TLS handshake, a DNS hiccup) are no longer logged: the next poll
  retries, and only a problem lasting 3 minutes is logged, with a note when it's reachable again. A
  rejected login is still logged at once. Connections get 20 seconds instead of 10.
- Overview and the Transcode queue warn when the app's CPU or memory limits are too low for 4K transcodes,
  say how much to give it, and point to where TrueNAS sets them (*Apps → MediaMan → Edit → Resources
  Configuration*).
- The setup wizard offers to create the Movies, Shows and downloads folders when they don't exist yet (as
  on a new install with an empty Media storage), and the folder picker has a *New folder…* button. Both
  only work inside the folders mounted into MediaMan.
- MediaMan is in the TrueNAS apps catalog: the README covers installing it from there (including
  switching Media Storage to a Host Path for an existing library), with the YAML install as the alternative.

## 1.8.2 — 2026-11-12

- Copies into the library show live progress: the Downloads page shows a progress bar on the download being
  copied (size, percentage, speed, time left), and Overview shows it on the downloads card. Moves between
  datasets show the same way. Speed and time left are measured over the last 10 seconds, so they settle to
  the disks' real speed instead of the burst at the start.

## 1.8.1 — 2026-11-10

- Fixed: HEVC transcodes (Dolby Vision and HDR10+ titles) dropped the HDR10 light levels (MaxCLL/MaxFALL)
  when the source kept them only in the Matroska container, as many disc remuxes do. They're now passed
  to x265 explicitly, as they already were for AV1.
- The check before a file is replaced now also refuses a result that lost the source's light levels.

## 1.8.0 — 2026-11-09

First public release.

- About section in Settings (version, license, source code, changelog, issues, and a Buy Me a Coffee
  link); Support section in the README.
- The README describes any download client (qBittorrent optional), Emby's shared layout, English folder
  names, and which hardware encoders are untested. `truenas/mediaman.yaml` installs the published image
  (`ghcr.io/mjwaters1985/mediaman`) instead of building on the NAS.
- Screenshots in `docs/screenshots/` (taken from a demo library of open-licence and public-domain
  titles; `tools/make-demo-library.sh` recreates it).
- New app icon: a blue gear with a gold ring around a bold "M" (SVG master plus 512 px, home-screen and
  browser-tab PNGs).
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
