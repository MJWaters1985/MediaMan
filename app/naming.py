"""Build Jellyfin-compliant destination paths.

Movies:  <movies>/Title (Year) [tmdbid-N]/Title (Year)[ - Edition][ - partN].ext
Shows:   <tv>/Series (Year) [tvdbid-N]/Season 01/Series S01E02[-E03][ - Episode Title].ext
"""
from __future__ import annotations

import os
import re

from .identify import COUNTRY_SUFFIX, SUB_EXT, norm, parse_folder_name

LANGS = {
    "english": "en", "eng": "en", "french": "fr", "fre": "fr", "fra": "fr", "german": "de", "ger": "de",
    "deu": "de", "spanish": "es", "spa": "es", "italian": "it", "ita": "it", "portuguese": "pt", "por": "pt",
    "dutch": "nl", "dut": "nl", "nld": "nl", "swedish": "sv", "swe": "sv", "norwegian": "no", "nor": "no",
    "danish": "da", "dan": "da", "finnish": "fi", "fin": "fi", "polish": "pl", "pol": "pl", "russian": "ru",
    "rus": "ru", "japanese": "ja", "jpn": "ja", "korean": "ko", "kor": "ko", "chinese": "zh", "chi": "zh",
    "zho": "zh", "arabic": "ar", "ara": "ar", "hebrew": "he", "heb": "he", "turkish": "tr", "tur": "tr",
    "greek": "el", "gre": "el", "czech": "cs", "cze": "cs", "hungarian": "hu", "hun": "hu", "romanian": "ro",
    "rum": "ro", "thai": "th", "vietnamese": "vi", "vie": "vi", "indonesian": "id", "ind": "id",
    "hindi": "hi", "hin": "hi", "ukrainian": "uk", "ukr": "uk", "brazilian": "pt-BR",
}


def clean(s: str) -> str:
    s = str(s).replace(":", " -").replace("/", "-").replace("\\", "-")
    s = re.sub(r'[<>"|?*\x00-\x1f]', "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.rstrip(". ") or "Unknown"


def _id_tag(ids: dict, prefer: tuple[str, ...]) -> str:
    for k in prefer:
        if ids.get(k):
            return f" [{k}id-{ids[k]}]"
    return ""


def find_existing_folder(root: str, title: str, year: int | None) -> str | None:
    """Reuse an existing library folder for the same title (avoids duplicate series/movie folders)."""
    if not root or not os.path.isdir(root):
        return None
    want = norm(title)
    want_bare = norm(COUNTRY_SUFFIX.sub("", title))
    best = None
    try:
        entries = os.listdir(root)
    except OSError:
        return None
    for name in entries:
        if name.startswith(".") or not os.path.isdir(os.path.join(root, name)):
            continue
        p = parse_folder_name(name)
        have = p.get("title") or ""
        if norm(have) != want:
            # "The Office (US)" folder vs TMDb's "The Office": only with matching years
            if not (year and p.get("year") == year and norm(COUNTRY_SUFFIX.sub("", have)) == want_bare):
                continue
        if year and p.get("year") and abs(int(p["year"]) - int(year)) > 1:
            continue
        if year and p.get("year") == year:
            return name
        best = best or name
    return best


def series_folder(info: dict, tv_root: str, include_ids: bool) -> str:
    existing = find_existing_folder(tv_root, info["title"], info.get("year"))
    if existing:
        info["library_match"] = existing
        return existing
    name = clean(info["title"])
    if info.get("year"):
        name += f" ({info['year']})"
    if include_ids:
        name += _id_tag(info.get("ids", {}), ("tvdb", "tmdb", "imdb"))
    return name


def movie_folder(info: dict, movies_root: str, include_ids: bool) -> str:
    existing = find_existing_folder(movies_root, info["title"], info.get("year"))
    if existing and info.get("year"):
        info["library_match"] = existing
        return existing
    name = clean(info["title"])
    if info.get("year"):
        name += f" ({info['year']})"
    if include_ids:
        name += _id_tag(info.get("ids", {}), ("tmdb", "imdb"))
    return name


def build_path(info: dict, ext: str, movies_root: str, tv_root: str, include_ids: bool = True,
               include_episode_title: bool = False) -> str | None:
    if not info.get("title"):
        return None
    ext = ext.lower()
    if info["type"] == "movie":
        folder = movie_folder(info, movies_root, include_ids)
        base = clean(info["title"]) + (f" ({info['year']})" if info.get("year") else "")
        if info.get("edition"):
            base += f" - {clean(info['edition'])}"
        if info.get("part"):
            base += f" - part{info['part']}"
        return os.path.join(movies_root, folder, base + ext)

    folder = series_folder(info, tv_root, include_ids)
    series = clean(info["title"])
    if info.get("date") and not info.get("episodes"):
        d = str(info["date"])[:10]
        return os.path.join(tv_root, folder, f"Season {d[:4]}", f"{series} {d}{ext}")
    if not info.get("episodes"):
        return None
    season = int(info.get("season") or 0)
    eps = info["episodes"]
    code = f"S{season:02d}E{eps[0]:02d}" + (f"-E{eps[-1]:02d}" if len(eps) > 1 else "")
    base = f"{series} {code}"
    if include_episode_title and info.get("episode_title"):
        base += f" - {clean(info['episode_title'])}"
    return os.path.join(tv_root, folder, f"Season {season:02d}", base + ext)


def sidecars(video: str) -> list[tuple[str, str]]:
    """Find subtitle/nfo sidecars. Returns (source_path, suffix) where suffix replaces the video stem,
    e.g. ('.../Movie.2010.en.forced.srt', '.en.forced.srt')."""
    vdir = os.path.dirname(video)
    stem = os.path.splitext(os.path.basename(video))[0]
    out: list[tuple[str, str]] = []
    try:
        names = os.listdir(vdir)
    except OSError:
        return out
    videos_here = [n for n in names if os.path.splitext(n)[1].lower() in
                   {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov", ".wmv"}]
    for n in names:
        p = os.path.join(vdir, n)
        ext = os.path.splitext(n)[1].lower()
        if p == video or not os.path.isfile(p):
            continue
        if n.startswith(stem + ".") and (ext in SUB_EXT or ext == ".nfo"):
            out.append((p, n[len(stem):]))
        elif ext in SUB_EXT and len(videos_here) == 1:
            # lone video with differently named subs (e.g. "English.srt")
            out.append((p, _lang_suffix(n)))
    # "Subs/" folders from release groups: Subs/2_English.srt or Subs/<episode stem>/3_English.srt
    for sub_dir in ("Subs", "subs", "Subtitles", "subtitles"):
        d = os.path.join(vdir, sub_dir)
        if not os.path.isdir(d):
            continue
        cand = os.path.join(d, stem)
        target = cand if os.path.isdir(cand) else (d if len(videos_here) == 1 else None)
        if not target:
            continue
        for n in sorted(os.listdir(target)):
            if os.path.splitext(n)[1].lower() in SUB_EXT:
                out.append((os.path.join(target, n), _lang_suffix(n)))
    # de-duplicate suffixes
    seen: dict[str, int] = {}
    result = []
    for src, suf in out:
        key = suf.lower()
        if key in seen:
            seen[key] += 1
            base, ext = os.path.splitext(suf)
            suf = f"{base}.{seen[key]}{ext}"
        else:
            seen[key] = 0
        result.append((src, suf))
    return result


def _lang_suffix(name: str) -> str:
    base, ext = os.path.splitext(name)
    tokens = [t.lower() for t in re.split(r"[\W_]+", base) if t]
    lang = next((LANGS[t] for t in tokens if t in LANGS), None)
    if not lang:
        lang = next((t for t in tokens if len(t) == 2 and t.isalpha()), None)
    flags = [f for f in ("forced", "sdh") if f in tokens]
    if "hi" in tokens and "sdh" not in flags:
        flags.append("sdh")
    parts = [lang] if lang else []
    return "." + ".".join(parts + flags) + ext.lower() if parts or flags else ext.lower()
