"""Film wishlist, phase 1b (v0.111.0): is the film on StashDB as SCENES?

StashDB has no group/movie type; a film shows up as scenes titled "<Series> <N> - Scene K" (sometimes "...: <Performer>"), often twice
(two studios). ONE queryScenes text request per check (the series name without its number; the number is matched locally). The scenes of
this very film are picked per "Scene K" (duplicates collapsed), their lengths are summed and compared with the film's length:
mode B ("put the film together from these scenes") is only offered at >= 80 % coverage (and not more than 120 %)."""
import logging
import re

import film_repo

logger = logging.getLogger(__name__)

OFFER_MIN_COVERAGE = 0.80
OFFER_MAX_COVERAGE = 1.20

_SCENE_RE = re.compile(r"^(?P<series>.*?)[\s:–-]*\bScene\s*(?P<k>\d+)\b\s*(?::\s*(?P<perf>.*))?$", re.I)
_VOL = re.compile(r"\b(?:vol(?:ume)?\.?|part|no\.?)\s*(?=\d)|#", re.I)
_TRAILING_NUMBER = re.compile(r"[\s#]*(?:(?:vol(?:ume)?\.?|part|no\.?)\s*)?\d+\s*$", re.I)

QUERY = """
query($input: SceneQueryInput!) {
  queryScenes(input: $input) {
    count
    scenes { id title date created duration details images { url } studio { id name }
             performers { performer { name } } }
  }
}
"""


def series_key(title):
    """Comparable form of a series + number: 'Breakin' 'Em In #14' and 'Breakin Em In 14' -> 'breakinemin14'."""
    return film_repo.norm_title(_VOL.sub(" ", title or ""))


def search_text(film_title):
    """The series name without its number -- StashDB's text search wants all words, so the number would hide the neighbouring films."""
    t = re.sub(r"[#'’`\"]", " ", film_title or "")
    t = _TRAILING_NUMBER.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_scene_title(title):
    m = _SCENE_RE.match((title or "").strip())
    if not m or not m.group("series").strip():
        return None
    return {"series": m.group("series").strip(), "k": int(m.group("k")), "performer": (m.group("perf") or "").strip() or None}


def _minutes(scene):
    return round((scene.get("duration") or 0) / 60, 1)


def evaluate(film_title, film_minutes, film_date, scenes):
    """Pure scoring of a StashDB result list (no request). Returns the check result dict."""
    key = series_key(film_title)
    by_k, other = {}, {}
    for s in scenes:
        parsed = parse_scene_title(s.get("title"))
        if not parsed:
            continue
        if series_key(parsed["series"]) == key:
            by_k.setdefault(parsed["k"], []).append((parsed, s))
        else:
            other.setdefault(parsed["series"], set()).add(parsed["k"])
    chosen = []
    for k in sorted(by_k):
        options = by_k[k]
        # duplicates of one "Scene k" (two studios / two submitters): the one with a length, then the closest date to the film
        def rank(item):
            _, s = item
            d = (s.get("date") or "")[:4]
            gap = abs(int(d) - int(film_date[:4])) if d.isdigit() and film_date and film_date[:4].isdigit() else 99
            return (0 if s.get("duration") else 1, gap, -len(s.get("performers") or []))
        parsed, s = min(options, key=rank)
        chosen.append({"k": k, "id": s["id"], "title": s.get("title"), "date": s.get("date"), "minutes": _minutes(s),
                       "studio": (s.get("studio") or {}).get("name"),
                       "performers": [p["performer"]["name"] for p in (s.get("performers") or []) if p.get("performer")],
                       "duplicates": len(options) - 1})
    total = round(sum(c["minutes"] for c in chosen), 1)
    coverage = round(total / film_minutes, 2) if film_minutes else None
    offer = bool(chosen) and coverage is not None and OFFER_MIN_COVERAGE <= coverage <= OFFER_MAX_COVERAGE
    if not chosen:
        message = "StashDB has no scenes of this movie" + (f" (it does have: {', '.join(sorted(other))})" if other else "") + "."
    elif coverage is None:
        message = f"{len(chosen)} scene(s) found, but the movie's length is unknown -- link the ThePornDB movie first."
    elif offer:
        message = f"{len(chosen)} scene(s), {total:.0f} of {film_minutes:.0f} min ({coverage:.0%}) -- enough to put the film together from them."
    elif coverage < OFFER_MIN_COVERAGE:
        message = (f"Only {len(chosen)} scene(s), {total:.0f} of {film_minutes:.0f} min ({coverage:.0%}) -- below "
                   f"{OFFER_MIN_COVERAGE:.0%}, so the movie is better searched as one file.")
    else:
        message = f"The scenes add up to {total:.0f} min ({coverage:.0%} of {film_minutes:.0f}) -- too long to be this film."
    return {"scenes": chosen, "minutes": total, "film_minutes": film_minutes, "coverage": coverage, "offer": offer, "message": message,
            "other_films": {name: sorted(ks) for name, ks in other.items()}}


def check(client, film):
    """ONE StashDB request for `film` (a film_repo row). Returns the evaluate() result plus the query and the hit count."""
    text = search_text(film["title"])
    resp = client._post({"query": QUERY, "variables": {"input": {"text": text, "page": 1, "per_page": 50}}})
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors"):
        raise RuntimeError(str(payload["errors"])[:300])
    result = (payload.get("data") or {}).get("queryScenes") or {}
    scenes = result.get("scenes") or []
    out = evaluate(film["title"], film.get("duration_min"), film.get("release_date"), scenes)
    out["query"] = text
    out["hits"] = result.get("count")
    out["incomplete"] = (result.get("count") or 0) > len(scenes)
    out["_scenes"] = {s["id"]: s for s in scenes if any(c["id"] == s["id"] for c in out["scenes"])}  # full records for use_scenes()
    return out


def put_on_wishlist(db, film, chosen):
    """Mode B: the film's StashDB scenes go on the SCENE wishlist (the normal search / grab / import / verification takes it from there).
    A scene that is already imported or already on the wishlist is left alone. Pseudo entity 'film:<id>' (like the upgrades' 'upgrade:')."""
    import release_repo
    added = []
    for c in chosen:
        rows = release_repo.list_rows_for_scene(db, "stashdb", c["id"])
        states = {r["status"] for r in rows}
        if states & {"imported", "sent_to_whisparr"}:
            continue
        if rows:
            release_repo.set_status_for_scene(db, "stashdb", c["id"], "sent_to_whisparr")
        else:
            scene = {"id": c["id"], "title": c.get("title"), "date": c.get("date"), "created": c.get("created") or film_repo.now(),
                     "studio": {"id": c.get("studio_id"), "name": c.get("studio")}, "image_url": c.get("image_url"), "is_vr": False,
                     "details": c.get("details") or ""}
            with db.connect() as conn:
                release_repo.insert_release(db, conn, scene, "stashdb", "studio", f"film:{film['id']}", status="sent_to_whisparr")
        added.append(c["id"])
    return added
