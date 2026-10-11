"""Series list for the Group page tab (v0.120.0): every movie of the series a group belongs to ("Breakin' 'Em In #14" -> all "Breakin' 'Em In N"),
each marked in the library / on the Movies wishlist / available. ONE ThePornDB /movies request per lookup (the series name without its
number, up to 50 results); "same series" = the title without its trailing number equals the group's."""
import re

import film_repo
import film_stashdb

_NUMBER = re.compile(r"(\d+)\s*$")


def _number(title):
    m = _NUMBER.search((title or "").replace("#", " ").strip())
    return int(m.group(1)) if m else None


_LOCAL_KEY = re.compile(r"^(.*?\S)(?:\s*#\s*|\s+(?:vol(?:ume)?\.?|part|no\.?)\s*|\s+)(\d+)\b", re.I)


def _local_keys(name):
    """Comparable keys of a local group name: the whole name, and -- the shop imports left junk behind the number ("Breakin' 'Em In #7
    - On Sale! One Day Flash Sale") -- the series plus the FIRST number."""
    keys = {film_stashdb.series_key(name)}
    m = _LOCAL_KEY.match((name or "").strip())
    if m:
        keys.add(film_stashdb.series_key(f"{m.group(1)} {m.group(2)}"))
    return keys


def lookup(client, group_name, per_page=50):
    """The raw ThePornDB records of the same series, sorted by number then year. Returns {"text", "records", "more"}."""
    text = film_stashdb.search_text(group_name)
    resp = client._get("/movies", params={"q": text, "per_page": per_page})
    resp.raise_for_status()
    results = resp.json().get("data") or []
    key = film_repo.norm_title(text)
    same = [r for r in results if key and film_repo.norm_title(film_stashdb.search_text(r.get("title"))) == key]
    # ThePornDB often has a second, thinner entry for the same number (no apostrophe in the title, 0 min, few performers, a later date):
    # keep the best one per number -- longest length, then most performers -- and remember how many were folded away.
    best, folded = {}, {}
    rest = []
    for r in same:
        n = _number(r.get("title"))
        if n is None:
            rest.append(r)
            continue
        folded[n] = folded.get(n, 0) + 1
        rank = ((r.get("duration") or 0) > 0, r.get("duration") or 0, len(r.get("performers") or []))
        if n not in best or rank > best[n][0]:
            best[n] = (rank, r)
    records = [best[n][1] for n in sorted(best)] + rest
    for r in records:
        n = _number(r.get("title"))
        r["_folded"] = (folded.get(n, 1) - 1) if n is not None else 0
    return {"text": text, "records": records, "more": len(results) >= per_page}


def describe(db, records, local_groups):
    """Rows for the UI. `local_groups`: [{id, name, files}] of the Stash groups. state: library (a group with the same series key + number
    exists; files 0 = an empty group), wishlist (a Movies wishlist row), available."""
    by_key = {}
    for g in sorted(local_groups, key=lambda g: -g["files"]):  # a group with files wins over an empty one of the same movie
        for key in _local_keys(g["name"]):
            by_key.setdefault(key, g)
    items = []
    for rec in records:
        title = (rec.get("title") or "").strip()
        date = rec.get("date") or ""
        year = int(date[:4]) if date[:4].isdigit() else None
        group = by_key.get(film_stashdb.series_key(title))
        wanted = film_repo.find_duplicate(db, rec.get("id"), title, year)
        state = "library" if group and group["files"] else ("wishlist" if wanted else ("empty-group" if group else "available"))
        items.append({"tpdb_id": rec.get("id"), "title": title, "year": year, "number": _number(title),
                      "minutes": round((rec.get("duration") or 0) / 60, 1) or None, "site": (rec.get("site") or {}).get("name"),
                      "image_url": rec.get("poster") if isinstance(rec.get("poster"), str) else None,
                      "url": film_repo.tpdb_url(rec.get("slug")), "state": state, "folded": rec.get("_folded") or 0,
                      "group_id": group["id"] if group else None, "group_files": group["files"] if group else None, "film_id": wanted})
    return items
