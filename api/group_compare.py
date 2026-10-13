"""Maintenance > Movies > Compare groups (v0.213.0): which video of the movie folders belongs to which EMPTY group.

A film folder without a group (library_movies.videos_without_group()) and a group without any file (groups_without_files()) often are
the same film under slightly different names ("Tonight's Girlfriend 19" / "Tonight's Girlfriend Vol. 19", "The Perfectionist" /
"Perfectionist, The"). This module only SUGGESTS: the user decides per pair ("Add to that group" / "Not this"); nothing is changed here.

Matching: both titles are normalised (accents, brackets, "Vol." / "Lesson" / "Part", "#", "&", trailing ", The", roman numerals); the
numbers in the title must be the same (Secretaries 2 is not Secretaries 3), except that one side may lack a "1"; the words must be
similar (ratio >= MIN_SCORE). The year is no condition (group dates are often wrong), a difference is only noted.
"""
import difflib
import re
import unicodedata

MIN_SCORE = 0.8
MAX_CANDIDATES = 3
_ARTICLES = ("the", "a", "an", "le", "la", "les", "der", "die", "das")
_NOISE = {"vol", "vols", "volume", "lesson", "part", "pt", "no", "nr", "number", "episode", "ep"}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12}


def normalise(title):
    """(words, numbers): the comparable words of a title and the numbers in it (years excluded)."""
    t = unicodedata.normalize("NFKD", str(title or "")).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", t)
    m = re.match(r"^(.*?),\s*(the|a|an|le|la|les|der|die|das)\s*$", t.strip())
    if m:
        t = f"{m.group(2)} {m.group(1)}"
    t = t.replace("&", " and ").replace("#", " ")
    tokens = re.sub(r"[^a-z0-9]+", " ", t).split()
    while tokens and tokens[0] in _ARTICLES:
        tokens = tokens[1:]
    words, numbers = [], []
    for i, tok in enumerate(tokens):
        if tok in _NOISE:
            continue
        if tok.isdigit():
            if not (len(tok) == 4 and 1900 <= int(tok) <= 2100):
                numbers.append(int(tok))
        elif tok in _ROMAN and i == len(tokens) - 1 and words:  # a trailing "II" is a number, "x" or "i" inside a title is not
            numbers.append(_ROMAN[tok])
        else:
            words.append(tok)
    return words, numbers


def compare(a_title, b_title):
    """(score, note) when the two titles may be the same film, else None. note: what is not exactly equal (numbers), or ''."""
    wa, na = normalise(a_title)
    wb, nb = normalise(b_title)
    if not wa or not wb:
        return None
    note = ""
    if na != nb:
        if (not na and nb == [1]) or (not nb and na == [1]):
            note = "one of them has no number, the other \"1\""
        else:
            return None
    score = difflib.SequenceMatcher(None, " ".join(wa), " ".join(wb)).ratio()
    return (round(score, 2), note) if score >= MIN_SCORE else None


def folder_key(folder):
    return f"{folder.get('root', '')}/{folder.get('folder', '')}"


def ignored_pairs(db):
    with db.connect() as conn:
        return {(r["folder_key"], r["group_id"]) for r in conn.execute("SELECT folder_key, group_id FROM group_match_ignore")}


def set_ignored(db, key, group_id, ignored=True):
    with db.connect() as conn:
        if ignored:
            conn.execute("INSERT OR IGNORE INTO group_match_ignore (folder_key, group_id) VALUES (?, ?)", (key, str(group_id)))
        else:
            conn.execute("DELETE FROM group_match_ignore WHERE folder_key = ? AND group_id = ?", (key, str(group_id)))


def build(db, folders, groups):
    """{pairs: [{folder, root, title, year, minutes, videos, key, candidates: [{group, score, note, year_note}]}],
        unmatched_groups: [...], ignored: n}. `folders` = videos_without_group(), `groups` = groups_without_files()."""
    skip = ignored_pairs(db)
    pairs, used, ignored = [], set(), 0
    for f in folders:
        key = folder_key(f)
        cands = []
        for g in groups:
            hit = compare(f["title"], g["name"])
            if not hit:
                continue
            if (key, str(g["id"])) in skip:
                ignored += 1
                continue
            year_note = ""
            gy = (g.get("date") or "")[:4]
            if f.get("year") and gy.isdigit() and gy != "0001" and abs(int(f["year"]) - int(gy)) > 1:
                year_note = f"year {f['year']} (folder) / {gy} (group)"
            cands.append({"group": g, "score": hit[0], "note": hit[1], "year_note": year_note})
        if cands:
            cands.sort(key=lambda c: (-c["score"], c["group"]["name"]))
            used.update(str(c["group"]["id"]) for c in cands)
            pairs.append({"key": key, "folder": f["folder"], "root": f["root"], "title": f["title"], "year": f.get("year"),
                          "minutes": f.get("minutes"), "videos": f["videos"], "candidates": cands[:MAX_CANDIDATES]})
    pairs.sort(key=lambda p: (-p["candidates"][0]["score"], p["title"].lower()))
    return {"pairs": pairs, "unmatched_groups": [g for g in groups if str(g["id"]) not in used], "ignored": ignored}
