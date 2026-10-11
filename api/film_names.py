"""Folder / file base name of a film: "<Title> (<Year>)" -- the layout of the film import (film_import.folder_name) and of the library's Movies/ folder.
Used for a Stash GROUP (a movie scene that was grouped by hand or by a scraper): the title may already end in " (YYYY)" and may be sorted
("Example Parody, The"); the year comes from that suffix or from the group's date."""
import re

_YEAR_SUFFIX = re.compile(r"\s*\((\d{4})\)\s*$")
_SORTED_ARTICLE = re.compile(r"^(.*),\s*(The|A|An)$", re.I)


def safe(name):
    t = re.sub(r'[\\/:*?"<>|]', " ", name or "")
    return re.sub(r"\s+", " ", t).strip().rstrip(".")


MAX_BASE_BYTES = 150  # "<Title> (<Year>)" is the folder name AND the start of the file name (+ " - part N", the quality, the extension): well below 255 bytes


def with_year(title, year):
    """"<Title> (<Year>)" with the TITLE shortened (at a word boundary, "..." at the end) when the whole would exceed MAX_BASE_BYTES."""
    suffix = f" ({year})" if year else ""
    room = MAX_BASE_BYTES - len(suffix.encode("utf-8"))
    if len(title.encode("utf-8")) > room:
        cut = title.encode("utf-8")[:max(8, room - 3)].decode("utf-8", errors="ignore")
        if " " in cut and len(cut) > room // 2:
            cut = cut[:cut.rfind(" ")]
        title = cut.rstrip(" .-,") + "..."
    return f"{title}{suffix}"


def base_name(group_name, group_date=None):
    """"<Title> (<Year>)", the title shortened to fit (kept for callers that want just that; the templates below are what the library uses)."""
    title, year = split_title_year(group_name, group_date)
    return with_year(title, year)


# ---------- movie naming templates (v0.194.0) ----------
# The same template language as the scene formats (optional parts < >, replace-words rules; see file_naming.py), with movie tokens. The folder format
# starts with the library folder for films ("Movies/...") and may use any token; the defaults reproduce the layout before there were templates.
import file_naming  # noqa: E402

DEFAULT_FOLDER_FORMAT = "Movies/{Movie Title}< ({Movie Year})>"
DEFAULT_FILE_FORMAT = "{Movie Title}< ({Movie Year})>< - part {Part Number}> {Quality Full}"
MOVIE_TOKENS = [
    ("Movie Title", "The film's title (the group's name without a trailing year; \"Foo, The\" becomes \"The Foo\")."),
    ("Movie Year", "The release year (from the title's \"(YYYY)\" or the date); empty when unknown."),
    ("Part Number", "The scene number of a film with several scenes/discs (\"2\"); empty for a one-file film."),
    ("Studio Title", "The studio of the film, when known."),
    ("Quality Full", "Source and resolution, e.g. \"WEBDL-1080p\"."),
    ("Source", "The source part of the quality (\"WEBDL\")."),
    ("Resolution", "The resolution part of the quality (\"1080p\")."),
]


def split_title_year(group_name, group_date=None):
    """(title, year): the name without a trailing " (YYYY)", a sorted article put back in front; the year from that suffix, else from the date."""
    name = (group_name or "").strip()
    year = None
    m = _YEAR_SUFFIX.search(name)
    if m:
        year, name = m.group(1), name[:m.start()]
    elif group_date and re.match(r"^\d{4}", group_date):
        year = group_date[:4]
    m = _SORTED_ARTICLE.match(name.strip())
    if m:
        name = f"{m.group(2)} {m.group(1)}"
    return safe(name), year


def movie_tokens(title, year, part_number, quality_full, studio="", options=None):
    source, _, resolution = (quality_full or "").partition("-")
    tokens = {"Movie Title": title or "", "Movie Year": str(year) if year else "", "Part Number": str(part_number) if part_number else "",
              "Studio Title": studio or "", "Quality Full": quality_full or "", "Source": source if resolution else "", "Resolution": resolution}
    return file_naming.apply_replacements(tokens, (options or {}).get("replacements"))


def render_movie_path(folder_format, filename_format, tokens, extension):
    """The path of a film file RELATIVE to the library root: <folder format>/<file format><extension>."""
    return file_naming.render_path(folder_format or DEFAULT_FOLDER_FORMAT, filename_format or DEFAULT_FILE_FORMAT, tokens, extension, title_token="Movie Title")


def movie_roots(library_root, folder_format):
    """The library folders that hold films: Movies/ and movies/ (as always) plus the first folder of the template, e.g. "/data/Films/"."""
    root = (library_root or "/data").rstrip("/")
    first = (folder_format or DEFAULT_FOLDER_FORMAT).split("/")[0]
    roots = [f"{root}/Movies/", f"{root}/movies/"]
    if first and "{" not in first and "<" not in first and f"{root}/{first}/" not in roots:
        roots.append(f"{root}/{first}/")
    return tuple(roots)
