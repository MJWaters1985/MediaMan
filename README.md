# MediaMan

A self-hosted media manager for a TrueNAS box running **Jellyfin** and **qBittorrent**. It sees the
same host paths those apps use and:

- **Scans your existing library**, works out what each file is, and proposes Jellyfin-correct folder
  and file names. Nothing moves until you approve (or set an auto-apply confidence).
- **Watches qBittorrent's finished folder**. A new download gets identified, **copied** into the
  right library folder under its Jellyfin name, subtitles included, and queued for transcoding.
  The original stays put so the torrent keeps seeding.
- **Runs a transcode queue** that picks the encoder from the hardware it detects (NVENC, Intel QSV,
  VAAPI, or CPU). It checks each result before replacing the original.

## How identification works

Each file gets a confidence score built from every hint available:

| Hint | Example |
|---|---|
| IDs already in the path | `Heat (1995) [imdbid-tt0113277]`, `{tmdb-603}` |
| `.nfo` files | `movie.nfo`, `tvshow.nfo`, `<episode>.nfo` (Kodi/Jellyfin XML or scene NFO with an IMDb link) |
| Existing library structure | `Shows/The Office (US)/Season 2/03 - Office Olympics.mkv` |
| Release / file names | `Severance.S01E03.1080p.ATVP.WEB-DL…`, season-pack folder names (via `guessit`) |
| Embedded tags | `show`, `season_number`, `episode_sort` |
| TMDb (optional API key) | Confirms the title and year, adds `[tmdbid-…]`, fills in episode titles |
| Existing library folders | Reuses `The Office (US) (2005) [tvdbid-73244]` so you don't get a duplicate series folder |

Samples, `Extras/`-style folders, and files under the minimum size are skipped. Downloads under the
auto-import confidence threshold (default 0.75) wait in **Downloads** for you to correct and import.

### Naming

```
Movies/Title (Year) [tmdbid-N]/Title (Year)[ - Edition][ - partN].ext
Shows/Series (Year) [tvdbid-N]/Season 01/Series S01E02[-E03][ - Episode Title].ext
Shows/Series/Season 00/Series S00E01.ext        (specials)
```

Sidecar subtitles move with the video. `Movie.en.forced.srt` keeps its language and flags, and
release-group `Subs/2_English.srt` becomes `Title (Year).en.srt`. When a movie is the only video in
its folder, the folder artwork (`poster.jpg`, `fanart.jpg`, `movie.nfo`, …) moves with it.

## Transcoding

The defaults are tuned for Blu-ray/UHD remuxes: keep the source resolution, use the most efficient
codec that keeps every bit of HDR, and target "no visible loss".

**Codec per title (`Codec = auto`):**

| Source | Output | Why |
|---|---|---|
| SDR, HDR10, HLG | AV1 (SVT-AV1, 10-bit) | most efficient; mastering display + MaxCLL/MaxFALL kept |
| Dolby Vision 7 / 8 | HEVC (x265) Dolby Vision **8.1** | the DV form today's TVs play; HDR10 base kept (a profile 7 FEL is dropped) |
| HDR10+ | HEVC (x265) + HDR10+ | dynamic metadata extracted and re-injected with `hdr10plus_tool`, remuxed with `mkvmerge` |
| DV profile 5, already-efficient HEVC/AV1 | left alone | |

HDR titles always use the CPU encoders: GPU encoders can't carry the HDR metadata.

**Quality:** with `vmaf` available, each title gets a short quality search. Four 8-second samples
are encoded at candidate CRFs and scored with Netflix VMAF (4K model for UHD). The highest CRF whose
weakest sample still reaches the target (default 95, visually lossless) wins. VMAF over-rates dark
HDR scenes, so HDR titles have a CRF ceiling: x265 ≤ 22, AV1 ≤ 28. A grainy film lands lower; for
example, *Goodfellas* got CRF 19.

**Streams:** every audio track is copied bit-for-bit, so TrueHD Atmos, DTS-HD MA, channel layouts,
languages and titles are unchanged. Every subtitle track, chapter and font is copied with its
language and default/forced flags. MP4 `mov_text` becomes SRT; broadcast caption streams are dropped.

**Before the original is replaced**, the result must have:

- a video stream, with a duration within 1% of the source
- the same audio tracks and channel counts, and the same number of subtitle tracks
- the same HDR transfer, plus the mastering display, Dolby Vision and HDR10+ metadata where the source had them
- a size at least `min savings` smaller

If any of these fail, the result is thrown away and the original stays. If they pass, the result
replaces the original under the same name (non-MKV sources become `.mkv`). The original is then
deleted, or moved to `.mediaman-trash` when *Keep originals* is on. Files in the downloads folder
are never transcoded.

**Performance note:** x265's automatic NUMA thread-pool setup fails inside Docker, which disables
its thread pool and wavefront parallelism. MediaMan passes `pools=<cpu count>` explicitly, which is
about 3.4× faster on a 24-thread Ryzen.

The queue can be paused and resumed, single jobs paused or cancelled, a job moved to the top, and a
time window set (e.g. `01:00-07:00`). Jobs interrupted by a restart go back into the queue.

## Install on TrueNAS (Community Edition 25.04+)

1. Copy this folder to the NAS, for example to `/mnt/<pool>/apps/mediaman-src`.
2. Build the image over SSH:

   ```sh
   sudo sh truenas/build-on-nas.sh
   ```

3. Edit `truenas/mediaman.yaml` and replace the `/mnt/POOL/...` paths with:
   - the folders your Jellyfin app mounts for Movies and Shows
   - qBittorrent's finished-downloads folder
   - a folder for MediaMan's config and database

   Check the `group_add` IDs with `ls -ln /dev/dri`. For NVIDIA, use the commented `deploy` block.
4. **Apps → Discover Apps → ⋮ → Install via YAML**, name it `mediaman`, and paste the YAML.
5. Open `http://<nas>:8765`. Check **Overview → Folders** (each one should show *writable*) and
   **Hardware**.
6. Optional, under **Settings**:
   - TMDb API key: better matching and IDs.
   - Jellyfin URL and API key: library refresh after imports and transcodes.
   - qBittorrent Web UI login: only import torrents that qBittorrent reports complete.

### HTTPS

Set `MEDIAMAN_TLS_CERT` and `MEDIAMAN_TLS_KEY` to a PEM certificate (full chain) and key that
the container can read. The UI is then served over HTTPS on the same port. MediaMan checks the
files every 5 minutes. When a renewal tool replaces them, it waits until no transcode, scan or
import is running, checks the new pair loads, and restarts itself onto it. If the files are
missing or don't load at startup, it falls back to plain HTTP and shows the reason on the
Overview tab, so a bad path can't lock you out.

### Permissions

The container runs as `568:568` (TrueNAS's `apps` user, the same user the Jellyfin and qBittorrent
apps use). That user needs write access to the library folders and read access to the downloads
folder. MediaMan never runs `chmod`/`chown`, and copies don't preserve the source permissions, so
new files inherit the library dataset's ACL. This matters on SMB/ACL-managed datasets, where
`chmod` is refused.

### Copy vs hardlink

The default `copy` works across any mounts. `hardlink` saves space but only works when downloads
and the library are on the **same** host-path mount inside the container. If you want that, mount
a common parent folder once and point the three folder settings at subfolders. Transcoding replaces
the library file with a new one, so a hardlinked download is never affected.

## Development

```sh
docker build -t mediaman:dev .
docker run --rm -v "$PWD":/src -w /src mediaman:dev sh -c "pip install -q pytest && python -m pytest -q tests"
./run-test.sh          # runs the app against ./testdata on http://localhost:8765
```

The code lives in `app/`: `identify.py` (hints), `naming.py` (Jellyfin paths and sidecars),
`organizer.py` (scan, propose, apply), `watcher.py` (downloads), `transcode.py` (hardware
detection, planning, queue), `integrations.py` (TMDb, Jellyfin, qBittorrent), `main.py` (API), and
`static/` (UI).
