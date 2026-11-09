"""Work out what a media file is from every hint available.

Hints, roughly in order of trust:
  * IDs already in the path ([tmdbid-603], {imdb-tt0133093}) or in an .nfo file
  * an existing library folder (TV root series folder, Season folders)
  * the release/file name (guessit), including the torrent's top-level folder name
  * embedded container tags (show / season_number / episode_sort)
  * a TMDb lookup (optional, see tmdb.py) to confirm and canonicalise
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

from guessit import guessit

VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".mpg", ".mpeg",
             ".webm", ".flv", ".vob", ".ogm", ".divx", ".3gp"}
SUB_EXT = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup", ".smi"}
EXTRAS_DIRS = {"extras", "featurettes", "behind the scenes", "deleted scenes", "interviews", "scenes",
               "shorts", "trailers", "samples", "sample", "other", "clips", "specials-extras"}
ARTWORK = re.compile(r"^(poster|folder|cover|fanart|backdrop\d*|banner|logo|clearlogo|clearart|disc|landscape|thumb|movie)\.(jpg|jpeg|png|nfo|tbn|webp)$", re.I)

SEASON_DIR = re.compile(r"^(?:season|series|staffel|saison|temporada|s)[ ._-]*(\d{1,4})\b", re.I)
SPECIALS_DIR = re.compile(r"^(specials?|season[ ._-]*0+)$", re.I)
ID_BRACKET = re.compile(r"[\[{](tmdbid|tvdbid|imdbid|tmdb|tvdb|imdb)[-=]([a-z0-9]+)[\]}]", re.I)
IMDB_RE = re.compile(r"\b(tt\d{7,9})\b")
FOLDER_YEAR = re.compile(r"^(?P<title>.+?)[ ._]*\((?P<year>(19|20)\d\d)\)")
COUNTRY_SUFFIX = re.compile(r"\s*\((US|UK|AU|NZ|CA|IE|DE|FR|JP|KR|NL|SE|DK|NO|BE|ES|IT|MX|BR|IN)\)$")
EDITION_RE = re.compile(r"\b(final[ ._]cut|director'?s[ ._]cut|extended[ ._](?:cut|edition)|theatrical[ ._]cut|"
                        r"ultimate[ ._](?:cut|edition)|special[ ._]edition|collector'?s[ ._]edition|"
                        r"anniversary[ ._]edition|unrated|uncut|imax|criterion|remastered)\b", re.I)
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "i": 1, "ii": 2, "iii": 3, "iv": 4}


def is_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXT


def is_sample(path: str) -> bool:
    name = os.path.basename(path).lower()
    parts = {p.lower() for p in path.split(os.sep)}
    return bool(re.search(r"(^|[\W_])sample([\W_]|$)", name)) or bool(parts & {"sample", "samples"})


def in_extras(rel: str) -> bool:
    return any(p.lower() in EXTRAS_DIRS for p in rel.split(os.sep)[:-1])


def _guess(text: str, typ: str | None = None) -> dict:
    opts = {"type": typ} if typ else {}
    try:
        g = guessit(text, opts)
    except Exception:
        return {}
    out = {}
    for k, v in g.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, list):
            out[k] = [x if isinstance(x, (str, int, float)) else str(x) for x in v]
        else:
            out[k] = str(v)
    return out


def _title_case(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip(" -._")
    if t and t == t.lower():
        small = {"a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "vs"}
        words = t.split(" ")
        t = " ".join(w if (i and w in small) else w[:1].upper() + w[1:] for i, w in enumerate(words))
    return t


def norm(t: str | None) -> str:
    t = (t or "").lower().replace("&", "and")
    t = re.sub(r"^(the|a|an)\s+", "", t)
    return re.sub(r"[^a-z0-9]", "", t)


def ids_from_text(text: str) -> dict:
    ids = {}
    for kind, val in ID_BRACKET.findall(text):
        kind = kind.lower().replace("id", "")
        ids[kind] = val
    m = IMDB_RE.search(text)
    if m and "imdb" not in ids:
        ids["imdb"] = m.group(1)
    return ids


def read_nfo(path: str) -> dict:
    """Parse Kodi/Jellyfin style NFO (XML) or a scene NFO containing an IMDb link."""
    try:
        with open(path, "rb") as f:
            raw = f.read(512_000)
    except OSError:
        return {}
    text = raw.decode("utf-8", "ignore")
    out: dict = {}
    try:
        root = ET.fromstring(text.strip())
        def t(tag):
            el = root.find(tag)
            return el.text.strip() if el is not None and el.text else None
        out["root"] = root.tag
        out["title"] = t("title")
        out["showtitle"] = t("showtitle")
        y = t("year") or (t("premiered") or t("aired") or "")[:4]
        out["year"] = int(y) if y and y.isdigit() else None
        for tag in ("season", "episode"):
            v = t(tag)
            out[tag] = int(v) if v and v.lstrip("-").isdigit() else None
        ids = {}
        for u in root.findall("uniqueid"):
            if u.text and u.get("type"):
                ids[u.get("type").lower()] = u.text.strip()
        for tag, key in (("imdbid", "imdb"), ("tmdbid", "tmdb"), ("tvdbid", "tvdb"), ("imdb_id", "imdb")):
            v = t(tag)
            if v:
                ids.setdefault(key, v)
        out["ids"] = ids
    except ET.ParseError:
        out["ids"] = ids_from_text(text)
    return {k: v for k, v in out.items() if v}


def _first_int(v):
    if isinstance(v, list):
        v = v[0] if v else None
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _episodes(v) -> list[int]:
    if v is None:
        return []
    if not isinstance(v, list):
        v = [v]
    out = []
    for x in v:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            pass
    return sorted(set(out))


def parse_folder_name(name: str) -> dict:
    """'The Office (US) (2005) [tvdbid-73244]' -> title/year/ids."""
    ids = ids_from_text(name)
    clean = ID_BRACKET.sub("", name).strip()
    m = FOLDER_YEAR.match(clean)
    if m:
        return {"title": m.group("title").strip(" -._"), "year": int(m.group("year")), "ids": ids}
    if " " in clean or "." not in clean:
        # human-named library folder ("The Office (US)") — trust it verbatim
        return {"title": clean.strip(" -._"), "year": None, "ids": ids}
    g = _guess(clean)   # release-style folder name ("Show.Name.S01.1080p")
    return {"title": g.get("title") or clean, "year": g.get("year"), "ids": ids}


def identify(path: str, root: str | None = None, root_kind: str | None = None,
             tags: dict | None = None) -> dict:
    """Return an identification dict. root_kind: 'movies' | 'tv' | None (downloads/unsorted)."""
    rel = os.path.relpath(path, root) if root else os.path.basename(path)
    parts = rel.split(os.sep)
    dirs, fname = parts[:-1], parts[-1]
    signals: list[str] = []
    forced = {"movies": "movie", "tv": "episode"}.get(root_kind or "")

    clean_rel = ID_BRACKET.sub("", rel)
    g = _guess(clean_rel, forced)
    gf = _guess(fname, forced)          # file name alone, for scene-style details
    kind = forced or g.get("type") or "movie"
    info: dict = {
        "type": kind,
        "title": g.get("title"),
        "year": g.get("year"),
        "season": _first_int(g.get("season")),
        "episodes": _episodes(g.get("episode")),
        "episode_title": g.get("episode_title"),
        "date": g.get("date"),
        "edition": None,
        "part": _first_int(g.get("cd")),
        "resolution": g.get("screen_size"),
        "ids": {},
    }
    if g.get("country") and info["title"]:
        info["title"] = f"{info['title']} ({g['country']})"
    if kind == "movie":
        m = EDITION_RE.search(fname)
        if m:
            info["edition"] = _title_case(re.sub(r"[ ._]+", " ", m.group(1)).lower()).replace("Directors", "Director's")
        elif g.get("edition"):
            ed = g["edition"] if isinstance(g["edition"], str) else " ".join(g["edition"])
            info["edition"] = ed if re.search(r"(cut|edition)$", ed, re.I) else ed + " Edition"
        if g.get("part") and info["title"]:
            # "Dune Part Two", "Deathly Hallows Part 2": part of the title, not a multi-file stack
            m = re.search(r"\b(?:part|pt)[ ._-]*([0-9]+|one|two|three|four|five|iv|iii|ii|i)\b", fname, re.I)
            word = m.group(1) if m else str(g["part"])
            word = word.upper() if word.lower() in ("i", "ii", "iii", "iv") else word.capitalize()
            info["title"] += f" Part {word}"
    if any(k in gf for k in ("screen_size", "source", "video_codec", "release_group")):
        signals.append("release-name")

    # ---- IDs embedded in the path
    ids = ids_from_text(rel)
    if ids:
        info["ids"].update(ids)
        signals.append("path-ids")

    # ---- existing library folder structure
    season_idx = None
    for i, d in enumerate(dirs):
        if SPECIALS_DIR.match(d):
            season_idx, info["season"] = i, 0
        else:
            m = SEASON_DIR.match(d)
            if m and (d.lower().startswith(("season", "series", "staffel", "saison", "temporada"))
                      or re.fullmatch(r"s\d{1,2}", d, re.I)):
                season_idx = i
                if info["season"] is None:
                    info["season"] = int(m.group(1))
    if season_idx is not None:
        info["type"] = kind = "episode"
        signals.append("season-folder")
    if root_kind == "tv" and dirs:
        series = parse_folder_name(dirs[0])
        if series.get("title"):
            info["title"], info["year"] = series["title"], series.get("year") or info["year"]
            info["ids"].update(series["ids"])
            signals.append("library-series-folder")
    elif kind == "episode" and season_idx:
        series = parse_folder_name(dirs[season_idx - 1])
        if series.get("title") and not info["title"]:
            info["title"] = series["title"]
    if root_kind == "movies" and dirs:
        mf = parse_folder_name(dirs[0])
        if mf.get("year"):
            info["title"], info["year"] = mf["title"], mf["year"]
            info["ids"].update(mf["ids"])
            signals.append("library-movie-folder")

    # ---- NFO files
    vdir = os.path.dirname(path)
    stem = os.path.splitext(path)[0]
    nfos = [stem + ".nfo"]
    if kind == "movie":
        nfos.append(os.path.join(vdir, "movie.nfo"))
    else:
        nfos += [os.path.join(vdir, "tvshow.nfo"), os.path.join(os.path.dirname(vdir), "tvshow.nfo")]
    for n in nfos:
        if os.path.isfile(n):
            nfo = read_nfo(n)
            if not nfo:
                continue
            signals.append("nfo")
            info["ids"].update({k: v for k, v in nfo.get("ids", {}).items() if k in ("tmdb", "tvdb", "imdb")})
            if nfo.get("root") == "tvshow" and nfo.get("title"):
                info["title"] = nfo["title"]
            elif nfo.get("root") == "movie" and nfo.get("title"):
                info["title"] = nfo["title"]
                info["year"] = nfo.get("year") or info["year"]
            elif nfo.get("root") == "episodedetails":
                info["season"] = nfo.get("season", info["season"])
                if nfo.get("episode") is not None:
                    info["episodes"] = [nfo["episode"]]
                info["episode_title"] = nfo.get("title") or info["episode_title"]
                if nfo.get("showtitle"):
                    info["title"] = nfo["showtitle"]

    # ---- embedded tags (only fill gaps)
    tags = tags or {}
    if kind == "episode":
        if not info["title"] and tags.get("show"):
            info["title"] = tags["show"]
            signals.append("embedded-tags")
        if info["season"] is None and str(tags.get("season_number", "")).isdigit():
            info["season"] = int(tags["season_number"])
        if not info["episodes"] and str(tags.get("episode_sort", "")).isdigit():
            info["episodes"] = [int(tags["episode_sort"])]
    if not info["year"] and str(tags.get("date", ""))[:4].isdigit() and kind == "movie":
        info["year"] = int(str(tags["date"])[:4])

    # ---- defaults & cleanup
    if kind == "episode" and info["season"] is None and info["episodes"]:
        info["season"] = 1
        signals.append("assumed-season-1")
    if info["title"]:
        info["title"] = _title_case(str(info["title"]))
    info["signals"] = signals
    info["confidence"] = score(info, root_kind)
    return info


def score(info: dict, root_kind: str | None) -> float:
    s = info.get("signals", [])
    c = 0.0
    if info["type"] == "movie":
        c += 0.4 if info.get("title") else 0
        c += 0.25 if info.get("year") else 0
        c += 0.1 if root_kind == "movies" else 0
    else:
        c += 0.35 if info.get("title") else 0
        c += 0.15 if info.get("season") is not None else 0
        c += 0.25 if (info.get("episodes") or info.get("date")) else 0
        c += 0.1 if root_kind == "tv" or "season-folder" in s else 0
        c -= 0.1 if "assumed-season-1" in s else 0
    c += 0.1 if "release-name" in s else 0
    if info.get("ids", {}).get("tmdb") or info.get("ids", {}).get("tvdb") or info.get("ids", {}).get("imdb"):
        c += 0.2
    if info.get("tmdb_match") is True:
        c += 0.25
    elif info.get("tmdb_match") is False:
        c -= 0.25
    if info.get("library_match"):
        c += 0.1
    return round(max(0.0, min(1.0, c)), 2)
