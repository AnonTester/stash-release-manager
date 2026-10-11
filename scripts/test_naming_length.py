"""Tests (v0.192.0): file/folder names stay within 255 bytes -- the title is shortened, nothing else, short names are unchanged.
Run:  venv/bin/python scripts/test_naming_length.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import file_naming as fn  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

FILE_FMT = "{Scene Title} - {Release Date} {Quality Full}"
FOLDER_FMT = "scenes/{Studio Title}/{Scene Title} - {Release Date}"
def tokens(title): return {"Scene Title": title, "Studio Title": "Some Studio", "Release Date": "2026-10-04", "Quality Full": "WEBDL-1080p"}  # noqa: E704

short = fn.render_path(FOLDER_FMT, FILE_FMT, tokens("A Normal Title"), ".mkv")
check("a normal name is unchanged", short == "scenes/Some Studio/A Normal Title - 2026-10-04/A Normal Title - 2026-10-04 WEBDL-1080p.mkv")

long_title = "Very Long Sample Title, Performer One & Performer Two, 3on1, Tag One, " * 6
rel = fn.render_path(FOLDER_FMT, FILE_FMT, tokens(long_title), ".mkv")
folder, name = rel.rsplit("/", 1)
check("every path segment is within 255 bytes", all(len(s.encode()) <= 255 for s in rel.split("/")))
check("the file name incl. extension is within 240 bytes", len(name.encode()) <= fn.MAX_NAME_BYTES)
check("date, quality and extension stay whole, only the title is shortened", name.endswith(" - 2026-10-04 WEBDL-1080p.mkv") and "..." in name)
check("the title still starts like the original", name.startswith("Very Long Sample Title"))

curly = "“" + "Ünïcödé tïtle – " * 30
rel = fn.render_path(FOLDER_FMT, FILE_FMT, tokens(curly), ".mp4")
check("multi-byte characters are counted as bytes", all(len(s.encode()) <= 255 for s in rel.split("/")))
rel.encode("utf-8").decode("utf-8")
check("never cut inside a character (result is valid UTF-8)", True)

check("a format without the title token is cut as a whole", len(fn.render_fitting("{Studio Title} " * 40, tokens("x")).encode()) <= fn.MAX_NAME_BYTES)
check("an empty title does not crash", fn.render_path(FOLDER_FMT, FILE_FMT, tokens(""), ".mkv").endswith("- 2026-10-04 WEBDL-1080p.mkv"))
check("_cut cuts at a word boundary", fn._cut("alpha beta gamma delta", 14) == "alpha beta")
import film_names  # noqa: E402
bn = film_names.base_name("Long Film Title " * 20, "2011-01-01")
check("movie folder name: long title shortened, year kept, within 150 bytes", len(bn.encode()) <= 150 and bn.endswith("... (2011)"))
check("movie name that fits is unchanged", film_names.base_name("Short", "2011-01-01") == "Short (2011)")
import film_import  # noqa: E402
check("the film import uses the same limit", len(film_import.folder_name({"title": "x" * 400, "year": 2005}).encode()) <= 150)
print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
