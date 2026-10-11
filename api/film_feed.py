"""New movies of the watched ThePornDB studios -> the Suggestions inbox of the Movies wishlist (Phase 3, v0.131.0).

ONE global feed (`/movies?orderBy=recently_created`) instead of a request per studio: a run reads the feed down to the stored watermark
(setting `feed_watermark:theporndb_movies`, the newest `created` seen), normally a single request. The first run only sets the watermark
(one request, no back catalogue). A movie becomes a 'suggested' row (never searched or grabbed by itself) when
- its studio (`site_id` == the watchlist's `native_id`) is a directly watched ThePornDB studio that is not on the "probably inactive" list,
- its release date is within the last `max_age_days` days (120) or still ahead -- a catalogue entry ThePornDB added late is not a new release,
- it is no VR studio (VR has its own pages),
- it is not known yet: no film_want row with the same ThePornDB id, or the same normalised title (+-1 year) in ANY status (a dismissed one
  stays dismissed), and no Stash group of the same title that already holds files.
"""
import logging
from datetime import date, datetime, timedelta, timezone

import film_repo
import film_series
import film_stashdb
import inactive_check
import watchlist_repo
from stashdb_client import FeedIncomplete

logger = logging.getLogger(__name__)

WATERMARK = "theporndb_movies"
MAX_AGE_DAYS = 120  # a movie dated older than this (or the release date missing nothing) is a catalogue entry ThePornDB added late, not a new release
NOTE = "New movie of a watched studio"


def stash_groups(stash_client):
    """[{id, name, files}] of every Stash group (one local GraphQL query)."""
    rows = stash_client._graphql("{ findGroups(filter: {per_page: -1}) { groups { id name scenes { files { id } } } } }", {})["findGroups"]["groups"]
    return [{"id": g["id"], "name": g["name"], "files": sum(len(s["files"]) for s in g["scenes"])} for g in rows]


def _known(db, rec, title, year):
    """The id of ANY film_want row (dismissed ones too) for this movie, else None."""
    with db.connect() as conn:
        r = conn.execute("SELECT id FROM film_want WHERE tpdb_id = ?", (rec.get("id"),)).fetchone()
        if r:
            return r["id"]
        key = film_repo.norm_title(title)
        if key:
            for r in conn.execute("SELECT id, title, year FROM film_want"):
                if film_repo.norm_title(r["title"]) == key and (not year or not r["year"] or abs(int(year) - int(r["year"])) <= 1):
                    return r["id"]
    return None


def _watched_studios(db, inactive_months):
    """{numeric site id: studio name} of the directly watched ThePornDB studios, without those on the probably-inactive list."""
    inactive = {x["local_id"] for x in inactive_check.list_review(db, inactive_months) if x["entity_type"] == "studio" and x["local_id"] is not None}
    return {str(r["native_id"]): r["name"] for r in watchlist_repo.list_watchlist(db, "theporndb", "studio")
            if r.get("native_id") and r.get("local_id") not in inactive}


def _recent_enough(rec, max_age_days):
    """True when the movie's release date is within the last `max_age_days` days or still ahead (no/unreadable date: kept)."""
    d = (rec.get("date") or "")[:10]
    try:
        return (date.today() - date.fromisoformat(d)).days <= max_age_days
    except ValueError:
        return True


def run(db, client, local_groups, inactive_months=18, max_age_days=MAX_AGE_DAYS, backfill_days=None):
    """One pass. `local_groups()` -> [{id, name, files}] of the Stash groups (asked only when there is a candidate). Returns a summary.
    `backfill_days`: a one-off look back -- the feed is read down to that many days ago instead of the stored watermark (the watermark then
    moves on as usual), the first run (baseline) is skipped."""
    wm = watchlist_repo.get_feed_watermark(db, WATERMARK)
    if backfill_days:
        wm = (datetime.now(timezone.utc) - timedelta(days=int(backfill_days))).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    if wm is None:
        newest = client.fetch_newest_movie_created()
        if newest:
            watchlist_repo.set_feed_watermark(db, WATERMARK, newest)
        return {"baseline": True, "requests": 1, "added": 0}
    studios = _watched_studios(db, inactive_months)
    seen, incomplete = [], False
    try:
        for rec in client.fetch_recent_movies(wm):
            seen.append(rec)
    except FeedIncomplete as e:
        incomplete = True
        logger.warning("movie feed: %s -- the movies in between are lost for the studio filter", e)
    result = {"seen": len(seen), "added": 0, "known": 0, "in_library": 0, "vr": 0, "old": 0, "added_titles": [], "incomplete": incomplete}
    candidates = [r for r in seen if str(r.get("site_id")) in studios]
    result["watched"] = len(candidates)
    groups = None
    for rec in candidates:  # newest first
        title = (rec.get("title") or "").strip()
        site_name = (rec.get("site") or {}).get("name") or studios[str(rec["site_id"])]
        if not title:
            continue
        if not _recent_enough(rec, max_age_days):
            result["old"] += 1
            continue
        if "vr" in site_name.lower():  # same name-based signal as the scene scan (ThePornDB has no VR flag)
            result["vr"] += 1
            continue
        fields = movie_fields(rec)
        if _known(db, rec, title, fields["year"]):
            result["known"] += 1
            continue
        if groups is None:
            by_key = {}
            for g in local_groups():
                if g["files"]:
                    for key in film_series._local_keys(g["name"]):
                        by_key.setdefault(key, g)
            groups = by_key
        if groups.get(film_stashdb.series_key(title)):
            result["in_library"] += 1
            continue
        _, created = film_repo.add(db, **fields, source="watched-studio", source_ref=str(rec["site_id"]), status="suggested", note=NOTE)
        if created:
            result["added"] += 1
            result["added_titles"].append(f"{title} ({site_name})")
    if seen:
        watchlist_repo.set_feed_watermark(db, WATERMARK, max(r["created"] for r in seen))
    return result


def movie_fields(rec):
    """film_want columns from a ThePornDB /movies record (same mapping as routers/films._movie_fields)."""
    date = rec.get("date") or ""
    return {"title": (rec.get("title") or "").strip(), "year": int(date[:4]) if date[:4].isdigit() else None, "release_date": date or None,
            "studio_name": (rec.get("site") or {}).get("name"),
            "duration_min": round((rec.get("duration") or 0) / 60, 1) or None,
            "performers": [p.get("name") for p in (rec.get("performers") or []) if isinstance(p, dict) and p.get("name")],
            "image_url": rec.get("poster") if isinstance(rec.get("poster"), str) else None,
            "tpdb_id": rec.get("id"), "tpdb_json": rec}
