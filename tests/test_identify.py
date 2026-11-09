import os
import tempfile

os.environ["MEDIAMAN_CONFIG"] = tempfile.mkdtemp()

import pytest  # noqa: E402

from app import naming  # noqa: E402
from app.identify import identify, read_nfo  # noqa: E402

M, T = "/media/movies", "/media/tv"


def dest(path, root=None, kind=None, **kw):
    info = identify(path, root, kind)
    return info, naming.build_path(info, os.path.splitext(path)[1], M, T, **kw)


@pytest.mark.parametrize("path,root,kind,expected", [
    ("/downloads/The.Matrix.1999.1080p.BluRay.x264-GRP/The.Matrix.1999.1080p.BluRay.x264-GRP.mkv", "/downloads", None,
     "/media/movies/The Matrix (1999)/The Matrix (1999).mkv"),
    ("/downloads/Breaking.Bad.S02E05.720p.WEB-DL.mkv", "/downloads", None,
     "/media/tv/Breaking Bad/Season 02/Breaking Bad S02E05.mkv"),
    ("/downloads/Severance.S01.1080p.ATVP.WEB-DL/Severance.S01E03.1080p.mkv", "/downloads", None,
     "/media/tv/Severance/Season 01/Severance S01E03.mkv"),
    ("/downloads/Show.Name.S03E01E02.1080p.mkv", "/downloads", None,
     "/media/tv/Show Name/Season 03/Show Name S03E01-E02.mkv"),
    ("/media/movies/Alien (1979)/alien.mkv", M, "movies",
     "/media/movies/Alien (1979)/Alien (1979).mkv"),
    ("/media/movies/Blade Runner (1982) [tmdbid-78]/Blade.Runner.1982.Final.Cut.mkv", M, "movies",
     "/media/movies/Blade Runner (1982) [tmdbid-78]/Blade Runner (1982) - Final Cut.mkv"),
    ("/media/tv/The Office (US)/Season 2/03 - Office Olympics.mkv", T, "tv",
     "/media/tv/The Office (US)/Season 02/The Office (US) S02E03.mkv"),
    ("/media/tv/Firefly/Firefly - 1x04 - Shindig.avi", T, "tv",
     "/media/tv/Firefly/Season 01/Firefly S01E04.avi"),
    ("/media/tv/Doctor Who (2005)/Specials/Doctor.Who.2005.S00E02.mkv", T, "tv",
     "/media/tv/Doctor Who (2005)/Season 00/Doctor Who S00E02.mkv"),
    ("/downloads/Dune Part Two (2024)/Dune.Part.Two.2024.2160p.UHD.mkv", "/downloads", None,
     "/media/movies/Dune Part Two (2024)/Dune Part Two (2024).mkv"),
    ("/downloads/Harry.Potter.and.the.Deathly.Hallows.Part.2.2011.1080p.mkv", "/downloads", None,
     "/media/movies/Harry Potter and the Deathly Hallows Part 2 (2011)/Harry Potter and the Deathly Hallows Part 2 (2011).mkv"),
    ("/downloads/Aliens.1986.Special.Edition.1080p.BluRay.mkv", "/downloads", None,
     "/media/movies/Aliens (1986)/Aliens (1986) - Special Edition.mkv"),
    ("/downloads/Old.Movie.1960.cd2.avi", "/downloads", None,
     "/media/movies/Old Movie (1960)/Old Movie (1960) - part2.avi"),
])
def test_paths(path, root, kind, expected):
    info, d = dest(path, root, kind)
    assert d == expected, info


def test_confidence_levels():
    good, _ = dest("/downloads/Inception.2010.1080p.BluRay.x264-SPARKS.mkv", "/downloads")
    assert good["type"] == "movie" and good["year"] == 2010 and good["confidence"] >= 0.75
    vague, _ = dest("/downloads/video_final_v2.mkv", "/downloads")
    assert vague["confidence"] < 0.75


def test_ids_from_path():
    info, d = dest("/media/movies/Heat (1995) [imdbid-tt0113277]/Heat.mkv", M, "movies")
    assert info["ids"]["imdb"] == "tt0113277"
    assert d == "/media/movies/Heat (1995) [imdbid-tt0113277]/Heat (1995).mkv"


def test_existing_series_folder_reused(tmp_path):
    tv = tmp_path / "tv"
    (tv / "The Office (US) (2005) [tvdbid-73244]").mkdir(parents=True)
    info = identify("/downloads/The.Office.US.S05E10.720p.mkv", "/downloads", None)
    d = naming.build_path(info, ".mkv", str(tmp_path / "movies"), str(tv))
    assert d == str(tv / "The Office (US) (2005) [tvdbid-73244]" / "Season 05" / "The Office (US) S05E10.mkv")


def test_nfo(tmp_path):
    d = tmp_path / "movies" / "weird folder"
    d.mkdir(parents=True)
    (d / "movie.nfo").write_text("<movie><title>Arrival</title><year>2016</year>"
                                 "<uniqueid type=\"tmdb\">329865</uniqueid></movie>")
    v = d / "a.mkv"
    v.write_bytes(b"")
    info = identify(str(v), str(tmp_path / "movies"), "movies")
    assert info["title"] == "Arrival" and info["year"] == 2016 and info["ids"]["tmdb"] == "329865"
    p = naming.build_path(info, ".mkv", "/m", "/t")
    assert p == "/m/Arrival (2016) [tmdbid-329865]/Arrival (2016).mkv"
    assert read_nfo(str(d / "movie.nfo"))["root"] == "movie"


def test_sidecars(tmp_path):
    (tmp_path / "Movie.2010.mkv").write_bytes(b"")
    (tmp_path / "Movie.2010.en.srt").write_text("x")
    (tmp_path / "Movie.2010.en.forced.srt").write_text("x")
    subs = tmp_path / "Subs"
    subs.mkdir()
    (subs / "2_English.srt").write_text("x")
    (subs / "3_French.srt").write_text("x")
    got = sorted(s for _, s in naming.sidecars(str(tmp_path / "Movie.2010.mkv")))
    assert got == [".en.1.srt", ".en.forced.srt", ".en.srt", ".fr.srt"]


def test_clean_names():
    assert naming.clean('Star Wars: Episode IV / "A New Hope"?') == "Star Wars - Episode IV - A New Hope"
