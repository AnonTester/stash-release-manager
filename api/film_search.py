"""Automatic search for the movies on the Movies wishlist (v0.128.0, Phase 2).

Per movie (status 'wanted', one-file mode) at most `min_days` apart and `per_run` movies per run: 1-3 indexer queries (title + year, title,
studio + title -- the next one only when the previous found nothing plausible), every result judged by `judge()`. The best plausible result is
stored on the movie as `review_candidate` (Activity > Needs review: Accept = grab, Reject = never again for this movie); nothing is grabbed
by itself. No plausible result -> last_search_result 'no_match' (Activity > Stuck). Packs of several videos count when title and size fit
(the import checks the summed length afterwards).
"""
import difflib
import json
import logging
import re
import unicodedata
from datetime import date

import film_repo
import film_whisparr

logger = logging.getLogger(__name__)

_ARTICLES = {"the", "a", "an", "of", "and", "in", "on", "at", "to", "for", "with", "my", "your"}
_NOISE = {"vol", "volume", "part", "no", "number", "dvd", "xxx", "movie", "film"}
_PACK = re.compile(r"\b(pack|\d+ videos?|complete|collection)\b", re.I)
_MIN_MB_PER_MIN, _MAX_MB_PER_MIN, _MIN_GB = 3.0, 450.0, 0.2


def _ascii(text):
    return "".join(ch for ch in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(ch))


def clean_title(title):
    """'Beautiful Slit, the' -> 'The Beautiful Slit'; punctuation removed, single spaces."""
    t = (title or "").strip()
    m = re.match(r"^(.*),\s*(the|a|an)$", t, re.I)
    if m:
        t = f"{m.group(2).capitalize()} {m.group(1)}"
    t = re.sub(r"[^\w\s']", " ", _ascii(t))
    return re.sub(r"\s+", " ", t).strip()


def _words(text):
    return [w for w in re.findall(r"[a-z0-9]+", _ascii(text).lower().replace("'", ""))]


def title_words(title):
    """(significant words, numbers) of a movie title: 'Hard Candy 4' -> (['hard', 'candy'], ['4'])."""
    words, nums = [], []
    for w in _words(clean_title(title)):
        if w.isdigit():
            nums.append(str(int(w)))  # "04" == "4"
        elif w not in _ARTICLES and w not in _NOISE and len(w) > 1:
            words.append(w)
    return words, nums


_SCENE_DATE = re.compile(r"\b(?:19|20)\d\d[ ._-](?:0[1-9]|1[0-2])[ ._-](?:0[1-9]|[12]\d|3[01])\b")


_SCENE_DATE_YY = re.compile(r"\b\d\d[ ._-](?:0[1-9]|1[0-2])[ ._-](?:0[1-9]|[12]\d|3[01])\b")  # 11.06.28 (yy.mm.dd), the way studios date a scene release
_SCENE_MARK = re.compile(r"\b(?:sc|scene|sce)\s?\d{1,2}\b")


def _starts_with_performer(prefix, performers, min_ratio=0.7):
    """True when a word in front of the title is (nearly) a first or last name of one of the movie's performers ("Alisya" ~ "Alysa Gap")."""
    names = set()
    for p in performers or []:
        names.update(w for w in _words(p) if len(w) >= 4)
    return any(difflib.SequenceMatcher(None, w, n).ratio() >= min_ratio for w in prefix if len(w) >= 4 for n in names)


_CONTRACTION = {"s", "t", "d", "m", "ll", "re", "ve"}


def _join_contractions(tokens):
    """['joe', 's', 'movie'] -> ['joes', 'movie']: a release writes "Joe's" as Joe.s (the apostrophe becomes a separator), a title word has
    no apostrophe after title_words(); a lone 's' / 't' / 'll' ... belongs to the word before it."""
    out = []
    for t in tokens:
        if t in _CONTRACTION and out and out[-1] not in _CONTRACTION:
            out[-1] += t
        else:
            out.append(t)
    return out


def judge(film, candidate):
    """(ok, score, reason) for one indexer result against a movie. A result is plausible when
    - every significant word of the title is a word of the release name (dots/underscores/dashes count as spaces),
    - a number in the title (Vol. 2, 4, #6 ...) is in the name too and the name has no different "vol N",
    - a year in the name is the movie's year (+-1),
    - it is no sample/trailer, no single "Scene N" clip, and its size fits the movie's length (3 .. 450 MB per minute, >= 0.2 GB)."""
    name = candidate.get("title") or ""
    flat = " ".join(_join_contractions(_words(name)))
    tokens = set(flat.split())
    words, nums = title_words(film["title"])
    if not words and not nums:
        return False, 0, "title has no searchable words"
    # The title must stand in the name as one run of words (articles, "vol"/"part" ignored on both sides), a title number right after it:
    # "Hard Candy 4" is NOT "A.Mother.s.Love.1-8 ... Hard.Candy.Films ... Love.4".
    sig = [str(int(t)) if t.isdigit() and len(t) < 4 else t for t in flat.split() if t not in _ARTICLES and t not in _NOISE]
    at = next((i for i in range(len(sig) - len(words) + 1) if sig[i:i + len(words)] == words), -1) if words else 0
    if at < 0:
        missing = [w for w in words if w not in tokens and w not in flat.replace(" ", "")]
        return False, 0, f"missing in the name: {', '.join(missing)}" if missing else "the title words are not in a row in the name"
    if "megapack" in tokens or "mega" in tokens and "pack" in tokens:
        return False, 0, "a collection of many movies (MegaPack)"
    # The title belongs at the START of the name (studio, date, maybe a few more words before it); deep inside a long name it is one
    # entry of a performer/studio collection ("Some.Performer.PART.2.MegaPACK ... 2012.Some.Title").
    if at > (3 if _PACK.search(flat) else 6):
        return False, 0, f"the title only starts at word {at + 1} of the name (collection?)"
    after = sig[at + len(words): at + len(words) + 1 + len(nums)]
    for n in nums:
        if n not in after:
            return False, 0, f"number {n} does not follow the title"
    if nums:
        other = re.findall(r"\b(?:vol|volume|part)[ ._-]?(\d+)\b", name, re.I)
        if other and not set(other) & set(nums):
            return False, 0, f"another volume ({', '.join(other)})"
    is_pack = bool(_PACK.search(flat))
    if tokens & {"sample", "trailer", "teaser", "preview", "screenshots", "screenshot"}:
        return False, 0, "sample/trailer"
    # A full date in the name (2008.11.25) is how SCENES are released; a performer's name in front of the title ("Alisya_Anal.Gaping.Sluts")
    # names a scene of that performer. Collections (packs) are judged by their size below.
    if not is_pack and (_SCENE_DATE.search(name) or _SCENE_DATE_YY.search(name)):
        return False, 0, "dated like a single scene, not the movie"
    if not is_pack and _SCENE_MARK.search(flat):  # "...POV.13.Sc4..." / "Scene 5": one scene of the movie
        return False, 0, "a single scene, not the movie"
    if not is_pack and at > 0 and _starts_with_performer(sig[:at], film.get("performers")):
        return False, 0, "starts with a performer's name -- a single scene, not the movie"
    if tokens & {"bonus", "extras", "extra"} and not is_pack:
        return False, 0, "bonus material, not the movie"
    if "split" in tokens and tokens & {"scenes", "scene"}:  # "Split.Scenes": the movie cut into one file per scene, each its own release
        return False, 0, "one scene of a movie released split into scenes"
    if tokens & {"scene", "clip"} and not is_pack:  # names use "_" and "." as separators, so judge the cleaned words, not the raw name
        return False, 0, "a single scene, not the movie"
    year = film.get("year")
    years = [int(y) for y in re.findall(r"\b(19[89]\d|20[0-4]\d)\b", name)]
    year_match = False
    if year and years:
        if any(abs(y - int(year)) <= 1 for y in years):
            year_match = True
        else:
            return False, 0, f"year {years[0]} does not fit {year}"
    size = candidate.get("size") or 0
    gb = size / 1e9
    minutes = film.get("duration_min") or 0
    if size:
        if gb < _MIN_GB:
            return False, 0, f"only {gb:.2f} GB"
        if minutes:
            mb_per_min = size / 1e6 / minutes
            if mb_per_min < _MIN_MB_PER_MIN:
                return False, 0, f"too small for {minutes:.0f} min ({mb_per_min:.1f} MB/min)"
            if mb_per_min > _MAX_MB_PER_MIN and not is_pack:
                return False, 0, f"too big for {minutes:.0f} min ({mb_per_min:.0f} MB/min)"
    score = 10.0
    score += 3 if year_match else 0
    studio = (_words(film.get("studio_name") or "") or [""])[0]
    if studio and studio in tokens:
        score += 2
    res = re.search(r"\b(2160|1080|720|576|480)p?\b", name)
    score += {"2160": 2, "1080": 2, "720": 1}.get(res.group(1), 0) if res else 0
    score += min(gb, 10) * 0.1
    return True, round(score, 2), "plausible" + (" (pack)" if is_pack else "")


_RES_IN_NAME = re.compile(r"\b(2160|1080|720|576|480)p?\b")


def resolution_of(name):
    """The resolution a release name advertises (2160 / 1080 / 720 / 576 / 480), None when it names none."""
    m = _RES_IN_NAME.search(name or "")
    return int(m.group(1)) if m else None


def resolution_class(height, width=0):
    """The resolution class of a file: 1080 when its height reaches 972 or its width 1728 (a letterboxed 1920x800 is 1080p), 480 below 576."""
    h, w = height or 0, width or 0
    for r in (2160, 1080, 720, 576):
        if h >= r * 0.9 or w >= r * 16 / 9 * 0.9:
            return r
    return 480


def judge_upgrade(film, candidate, min_res, owned_height, owned_width=0):
    """judge() plus the upgrade rule (v0.222.0): the name must NAME a resolution that is at least the quality profile's smallest one and
    higher than the class of the file we own. A name without a resolution cannot be called better, so it is not taken."""
    ok, score, reason = judge(film, candidate)
    if not ok:
        return ok, score, reason
    res = resolution_of(candidate.get("title"))
    if res is None:
        return False, 0, "the name does not say its resolution, so it cannot be called better"
    if min_res and res < int(min_res):
        return False, 0, f"{res}p is below the quality profile's {min_res}p"
    owned = resolution_class(owned_height, owned_width)
    if res <= owned:
        return False, 0, f"{res}p is not better than the owned {owned}p file"
    return True, score + (res - owned) / 1000.0, f"better: {res}p instead of {owned}p"


def queries(film):
    """The queries in the order they are tried -- the next one only when the previous found nothing plausible."""
    title = clean_title(film["title"])
    year = film.get("year")
    out = [f"{title} {year}" if year else title]
    if year:
        out.append(title)
    studio = clean_title(film.get("studio_name") or "")
    if studio:
        out.append(f"{studio.split(' / ')[0].split()[0]} {title}")
    if "'" in title:  # releases write Joe's as Joes or Joe.s: the plain spelling is asked last (one more request, only for these titles)
        out.append(f"{title.replace(chr(39), '')} {year}" if year else title.replace(chr(39), ""))
    seen, uniq = set(), []
    for q in out:
        if q.lower() not in seen:
            seen.add(q.lower())
            uniq.append(q)
    return uniq


def _norm(name):
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def search_film(db, film, source, upgrade=None):
    """One movie, up to three queries. Returns {"result": 'needs_review' | 'no_match' | 'error', "candidate", "queries", "judged"}. Writes
    the outcome to the movie row (candidate / result / time). `upgrade` = {"min_res", "owned_height"}: a movie that is owned below the quality
    profile (Movies > Upgrade); only releases that are better than the owned file count."""
    rejected = {_norm(t) for t in (film.get("rejected_titles") or [])}
    asked, judged, best = [], [], None
    try:
        for q in queries(film):
            asked.append(q)
            results = source.search(q)
            plausible = []
            for r in results:
                if _norm(r.get("title")) in rejected:
                    continue
                ok, score, reason = judge_upgrade(film, r, upgrade.get("min_res"), upgrade.get("owned_height"), upgrade.get("owned_width", 0)) if upgrade else judge(film, r)
                judged.append({"title": r.get("title"), "ok": ok, "score": score, "reason": reason})
                if ok:
                    plausible.append((score, r))
            if plausible:
                plausible.sort(key=lambda x: (-x[0], x[1].get("age") if x[1].get("age") is not None else 1e9))
                best = plausible[0][1]
                break
    except Exception as e:  # noqa: BLE001
        logger.exception("film search failed for %s", film["title"])
        film_repo.update(db, film["id"], last_search_at=film_repo.now(), last_search_result="error")
        return {"result": "error", "error": f"{type(e).__name__}: {e}", "queries": asked, "judged": judged}
    if best is None:
        film_repo.update(db, film["id"], last_search_at=film_repo.now(), last_search_result="no_match", review_candidate=None)
        return {"result": "no_match", "queries": asked, "judged": judged}
    cand = {k: best.get(k) for k in ("title", "size", "age", "indexer", "indexerId", "downloadUrl", "infoUrl", "guid", "protocol", "magnetUrl", "infoHash", "seeders", "peers", "seedRatio", "seedTime") if k in best}
    film_repo.update(db, film["id"], last_search_at=film_repo.now(), last_search_result="needs_review", review_candidate=cand)
    return {"result": "needs_review", "candidate": cand, "queries": asked, "judged": judged}


def _is_recent(f, recent_days):
    """True when the movie's release date is within the last `recent_days` days (or still ahead)."""
    d = (f.get("release_date") or "")[:10]
    if not d:
        return False
    try:
        return (date.today() - date.fromisoformat(d)).days <= recent_days
    except ValueError:
        return False


def due_films(db, per_run=5, min_days=7, only_ids=None, recent_days=0, recent_min_days=None):
    """Movies to search now: wanted, one-file mode, no candidate waiting, never searched or searched more than `min_days` ago -- never
    searched first, then the longest ago."""
    rows = film_repo.list_films(db, ["wanted"], per_page=0)["rows"]
    now_s = film_repo.now()
    from routers.settings import resolve_whisparr_scope_preset
    scope = resolve_whisparr_scope_preset(db)
    out = []
    for f in rows:
        if only_ids is not None and f["id"] not in only_ids:
            continue
        # Where Whisparr searches (search_sidecar_import, full_whisparr) the movies registered there are Whisparr's to search, like the scenes
        # (stage C, v0.159.0); a manual "search now" (only_ids) is always allowed.
        if only_ids is None and film_whisparr.searched_by_whisparr(db, scope, f):
            continue
        if (f.get("mode") or "file") != "file" or f.get("review_candidate"):
            continue
        last = f.get("last_search_at")
        if last and only_ids is None:
            with db.connect() as conn:
                age = conn.execute("SELECT julianday(?) - julianday(?) AS d", (now_s, last)).fetchone()["d"]
            wait = recent_min_days if recent_min_days is not None and recent_days and _is_recent(f, recent_days) else min_days
            if age is not None and age < wait:
                continue
        out.append(f)
    out.sort(key=lambda f: (f.get("last_search_at") or ""))
    return out[: max(int(per_run), 0)] if only_ids is None else out


def due_upgrades(db, per_run=5, min_days=7, only_ids=None):
    """Owned movies below the quality profile (Movies > Upgrade) to search now: no candidate waiting, no download running, never searched
    or searched more than `min_days` ago. Same pacing as the wishlist."""
    now_s = film_repo.now()
    out = []
    for f in film_repo.list_films(db, ["imported"], per_page=0)["rows"]:
        if not f.get("upgrade_wanted") or f.get("review_candidate") or f.get("upgrading") or not f.get("imported_group_id"):
            continue
        if only_ids is not None and f["id"] not in only_ids:
            continue
        last = f.get("last_search_at")
        if last and only_ids is None:
            with db.connect() as conn:
                age = conn.execute("SELECT julianday(?) - julianday(?) AS d", (now_s, last)).fetchone()["d"]
            if age is not None and age < min_days:
                continue
        out.append(f)
    out.sort(key=lambda f: (f.get("last_search_at") or ""))
    return out[: max(int(per_run), 0)] if only_ids is None else out


def search_upgrade(db, film, source, stash, min_res):
    """The upgrade search of ONE owned movie: the owned file is looked at first (a Group with one scene and one file only -- anything else is
    replaced by hand), then search_film() with the upgrade rule. Returns the search_film() result or {"result": 'not_searched', "reason"}."""
    import film_import
    target = film_import.upgrade_target(stash, film["imported_group_id"])
    if target is None:  # the Group is gone: nothing to upgrade any more
        film_repo.update(db, film["id"], upgrade_wanted=0)
        return {"result": "not_searched", "reason": "the Stash Group does not exist any more"}
    if isinstance(target, str):
        film_repo.update(db, film["id"], last_search_at=film_repo.now(), last_search_result="upgrade_manual")
        return {"result": "not_searched", "reason": target}
    film_repo.update(db, film["id"], owned_height=target["height"])
    return search_film(db, film, source, upgrade={"min_res": min_res, "owned_height": target["height"], "owned_width": target["width"]})


def run(db, get_source, per_run=5, min_days=7, recent_days=0, recent_min_days=None, stash=None, min_res=None):
    """One pass: the due movies, one source for all. `get_source()` -> a source with .search(query) or None when none is configured. With
    `stash`, the owned movies below the quality profile are searched for a better release too (same per-run budget, own count)."""
    source = get_source()
    if source is None:
        return {"skipped": True, "reason": "no search source configured"}
    results = []
    for f in due_films(db, per_run, min_days, recent_days=recent_days, recent_min_days=recent_min_days):
        full = film_repo.get(db, f["id"])
        results.append({"id": f["id"], "title": f["title"], **{k: v for k, v in search_film(db, full, source).items() if k != "judged"}})
    upgrades = []
    if stash is not None:
        for f in due_upgrades(db, per_run, min_days):
            full = film_repo.get(db, f["id"])
            try:
                out = search_upgrade(db, full, source, stash, min_res)
            except Exception as e:  # noqa: BLE001
                logger.exception("upgrade search failed for %s", f["title"])
                out = {"result": "error", "error": f"{type(e).__name__}: {e}"}
            upgrades.append({"id": f["id"], "title": f["title"], **{k: v for k, v in out.items() if k != "judged"}})
    return {"searched": len(results), "found": sum(1 for r in results if r["result"] == "needs_review"), "results": results,
            "upgrades_searched": len(upgrades), "upgrades_found": sum(1 for r in upgrades if r["result"] == "needs_review"), "upgrades": upgrades}


def stuck_films(db):
    """The movies Activity > Stuck lists: on the wishlist, one-file mode, no candidate waiting, searched before with no usable result."""
    rows = [f for f in film_repo.list_films(db, ["wanted"], per_page=0)["rows"]
            if (f.get("mode") or "file") == "file" and not f.get("review_candidate")
            and f.get("last_search_result") in ("no_match", "rejected", "error")]
    rows.sort(key=lambda f: (f.get("last_search_at") or ""))
    return rows


def search_stuck(db, source):
    """"Search all stuck movies": every stuck movie once, now (search_film(): 1-3 queries each, a plausible release becomes a candidate in Needs
    review, nothing is grabbed). Returns {"searched", "found", "results"}."""
    results = []
    for f in stuck_films(db):
        out = search_film(db, film_repo.get(db, f["id"]), source)
        results.append({"id": f["id"], "title": f["title"], "result": out["result"]})
    return {"searched": len(results), "found": sum(1 for r in results if r["result"] == "needs_review"), "results": results}
