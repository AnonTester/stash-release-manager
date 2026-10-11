"""Tests (v0.194.0): movie naming templates -- the defaults reproduce the old layout, custom templates, optional year/part, tokens, replacements, the film
folders, the settings checks and the preview. No network. Run:  venv/bin/python scripts/test_movie_templates.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import film_names as fnm  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

def path(title="Sample Film", year="2011", part=None, quality="WEBDL-1080p", studio="", folder=None, name=None, options=None, ext=".mkv"):  # noqa: E302
    return fnm.render_movie_path(folder, name, fnm.movie_tokens(title, year, part, quality, studio, options), ext)

check("default: Movies/<Title> (<Year>)/<Title> (<Year>) <Quality>.<ext> (the layout from before the templates)", path() == "Movies/Sample Film (2011)/Sample Film (2011) WEBDL-1080p.mkv")
check("default with several parts: ' - part N'", path(part=2) == "Movies/Sample Film (2011)/Sample Film (2011) - part 2 WEBDL-1080p.mkv")
check("default without a year: just the title (the optional part vanishes)", path(year=None) == "Movies/Sample Film/Sample Film WEBDL-1080p.mkv")

check("a template with another library folder and the studio", path(studio="Some Studio", folder="Films/{Studio Title}/{Movie Title} ({Movie Year})", name="{Movie Title} ({Movie Year}) [{Resolution}]")
      == "Films/Some Studio/Sample Film (2011)/Sample Film (2011) [1080p].mkv")
check("an optional studio folder: dropped when the studio is unknown", path(folder="Movies/<{Studio Title}>/{Movie Title}", name="{Movie Title}") == "Movies/Sample Film/Sample Film.mkv"
      and path(studio="S", folder="Movies/<{Studio Title}>/{Movie Title}", name="{Movie Title}") == "Movies/S/Sample Film/Sample Film.mkv")
check("Source / Resolution tokens", path(name="{Movie Title} {Source} {Resolution}") == "Movies/Sample Film (2011)/Sample Film WEBDL 1080p.mkv")
check("replace-words rules apply to the movie title too", path(title="Dad's Film", options={"replacements": [{"find": "'", "replace": "", "mode": "text"}]}) == "Movies/Dads Film (2011)/Dads Film (2011) WEBDL-1080p.mkv")
check("illegal characters are replaced", path(title='What: A "Film"?') == "Movies/What A Film (2011)/What A Film (2011) WEBDL-1080p.mkv")
long = path(title="Very Long Title " * 30)
check("a very long title is shortened, the year and quality stay", all(len(p.encode()) <= 255 for p in long.split("/")) and long.endswith(" (2011) WEBDL-1080p.mkv") and "..." in long)

t, y = fnm.split_title_year("Example Parody, The (2012)", "2013-01-01")
check("title/year: the year in the name wins, the sorted article moves to the front", (t, y) == ("The Example Parody", "2012"))
check("title/year from the date when the name has none", fnm.split_title_year("Some Film", "2010-05-05") == ("Some Film", "2010") and fnm.split_title_year("Some Film") == ("Some Film", None))

check("film folders: Movies/ and movies/ always, plus the template's first folder", fnm.movie_roots("/data", "Films/{Movie Title}") == ("/data/Movies/", "/data/movies/", "/data/Films/")
      and fnm.movie_roots("/data/", None) == ("/data/Movies/", "/data/movies/")
      and fnm.movie_roots("/data", "{Studio Title}/{Movie Title}") == ("/data/Movies/", "/data/movies/"))

from fastapi import HTTPException  # noqa: E402
from db import Database  # noqa: E402
from routers import settings as rs  # noqa: E402
rs.init_settings_router(Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3")))
def put(key, value):  # noqa: E302
    try:
        rs.set_one(key, {"value": value}); return True
    except HTTPException:
        return False
check("good movie formats are stored", put("movie_folder_format", "Films/{Movie Title}") and put("movie_naming_format", "{Movie Title} {Quality Full}"))
check("empty / absolute / '..' / backslash folder formats are refused", not put("movie_folder_format", "") and not put("movie_folder_format", "/etc/{Movie Title}")
      and not put("movie_folder_format", "../x/{Movie Title}") and not put("movie_folder_format", "a\\b"))
check("a '/' in the file format is refused", not put("movie_naming_format", "a/{Movie Title}"))
check("the defaults in the settings are the module defaults", rs.DEFAULTS["movie_folder_format"] == fnm.DEFAULT_FOLDER_FORMAT and rs.DEFAULTS["movie_naming_format"] == fnm.DEFAULT_FILE_FORMAT)

rs.set_one("movie_folder_format", {"value": "Movies/{Movie Title}< ({Movie Year})>"})
rs.set_one("movie_naming_format", {"value": fnm.DEFAULT_FILE_FORMAT})
pv = rs.naming_preview({})
check("preview: movie examples with a year, a part number, no year", pv["movie_example"] == "Movies/Example Film Title (2011)/Example Film Title (2011) WEBDL-1080p.mkv"
      and pv["movie_example_part"].endswith("Example Film Title (2011) - part 2 WEBDL-1080p.mkv") and pv["movie_example_no_year"] == "Movies/Example Film Title/Example Film Title WEBDL-1080p.mkv")
pv = rs.naming_preview({"movie_folder_format": "Films/{Studio Title}/{Movie Title}", "movie_filename_format": "{Movie Title}"})
check("preview of an unsaved movie format", pv["movie_example"] == "Films/Example Studio/Example Film Title/Example Film Title.mkv")
print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
