"""On-demand backlog browsing for one Studio/Performer at a time
(2026-09-13, see PROGRESS.md's "On-demand backlog browsing, bypassing
Whisparr's Monitored flag" section).

Whisparr's own way to browse a Studio/Performer's full historical
catalog is its `monitored` flag, which pulls the *entire* known catalog
into Whisparr's own database as a side effect -- expensive for a large
backlog, and duplicates data this project already has no use for once
the user has picked what they actually want. Since hand-off itself
(handoff.py's send_to_whisparr()) already resolves a single scene by
its StashDB id via Whisparr's own on-demand `/movie/lookup` -- which
needs no pre-existing Studio/Performer monitoring at all -- there was
never a real dependency on Whisparr's Monitored flag for this. This
router instead fetches the backlog directly from StashDB (the same
source and client the regular scan already uses) and stores it locally
as status='backlog', so the existing hand-off/dismiss actions and
Releases-page rendering all just work against it unchanged.

StashDB only: hand-off itself only ever works for StashDB-sourced
scenes (see handoff.py), and ThePornDB's REST API has no comparable
date-range filter to build a year-by-year picker from -- see
stashdb_client.py's count_scenes_by_year()/fetch_scenes_for_year().

Never touches watchlist.py's watermark tracking -- that's the regular
incremental scan's job (watcher.py); this is a separate, on-demand read
scoped to one entity at a time, never run automatically for the whole
watchlist.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import scene_alias
import backlog_repo
import release_repo
import watchlist_repo
from routers.settings import resolve_whisparr_quality_profiles

router = APIRouter(prefix="/backlog", tags=["backlog"])

_db = None
_get_stashdb_client = None
_get_stash_client = None
_get_whisparr_client = None


def init_backlog_router(db, get_stashdb_client, get_stash_client, get_whisparr_client):
    """get_stashdb_client/get_stash_client/get_whisparr_client:
    zero-arg factories, not fixed instances -- same reasoning as every
    other client factory in main.py, a Settings-page edit to a
    connection's URL/API key/delay takes effect on the very next
    backlog request. get_whisparr_client is only used for the
    completeness check's quality-profile lookup (a small, fixed-size
    endpoint unrelated to Whisparr's own movie-library size) -- not
    the per-scene `/movie?stashId=` lookup dropped from
    routers/releases.py's library_status(), a different, heavier
    Whisparr call this module never needed."""
    global _db, _get_stashdb_client, _get_stash_client, _get_whisparr_client
    _db, _get_stashdb_client, _get_stash_client, _get_whisparr_client = (
        db, get_stashdb_client, get_stash_client, get_whisparr_client,
    )


def _check_stashdb_only(provider, entity_type):
    if provider != "stashdb":
        raise HTTPException(400, "Backlog browsing only supports StashDB-sourced entities")
    if entity_type not in ("studio", "performer"):
        raise HTTPException(400, "entity_type must be 'studio' or 'performer'")


@router.get("/{provider}/{entity_type}/{entity_stashdb_id}/years")
def years(provider: str, entity_type: str, entity_stashdb_id: str, refresh: bool = False):
    """Per-year scene-count preview, so the user can pick which year(s)
    to actually fetch before committing to it -- see
    stashdb_client.py's count_scenes_by_year() docstring for why a live
    check stays cheap (count-only queries, never actual scene rows)
    even against a large backlog.

    Cache-first (2026-09-13, see PROGRESS.md's "Cache the backlog year
    overview" section): returns the last-checked result from
    `backlog_overview` when one exists, with no StashDB requests at
    all, unless `refresh=true` or nothing has ever been cached for this
    entity -- either way, a fresh check is stored back for next time.
    """
    _check_stashdb_only(provider, entity_type)
    overview = None
    if not refresh:
        overview = backlog_repo.get_year_overview(_db, provider, entity_type, entity_stashdb_id)
    if overview is None:
        counts = _get_stashdb_client().count_scenes_by_year(entity_type, entity_stashdb_id)
        overview = backlog_repo.set_year_overview(_db, provider, entity_type, entity_stashdb_id, counts)
    # 2026-09-26: plus the entity's date window and the explicitly
    # loaded years, for the panel's window editor / "All" / markings.
    return {
        **overview,
        "window": _window(provider, entity_type, entity_stashdb_id),
        "loaded_years": {str(y): at for y, at in
                         backlog_repo.loaded_years(_db, provider, entity_type, entity_stashdb_id).items()},
    }


def _window(provider, entity_type, entity_stashdb_id):
    entry = watchlist_repo.get_entry(_db, provider, entity_type, entity_stashdb_id)
    if entry is None:
        return {"from": None, "until": None, "own_from": None, "from_is_default": False, "local_id": None}
    window_from, window_until, is_default = watchlist_repo.effective_window(_db, entry)
    return {"from": window_from, "until": window_until, "own_from": entry.get("auto_handoff_from"),
            "from_is_default": is_default, "local_id": entry.get("local_id")}


class WindowBody(BaseModel):
    from_date: str | None = None
    until_date: str | None = None


def _entity_rows(entity_type, local_id):
    return [r for r in watchlist_repo.list_watchlist(_db, None, entity_type) if r.get("local_id") == local_id]


@router.put("/{provider}/{entity_type}/{entity_stashdb_id}/window")
def set_window(provider: str, entity_type: str, entity_stashdb_id: str, body: WindowBody):
    """Sets the entity's date window (on all its provider rows) and
    reports how many OPEN releases now lie outside it -- the panel then
    offers to dismiss those (nothing is removed silently)."""
    entry = watchlist_repo.get_entry(_db, provider, entity_type, entity_stashdb_id)
    if entry is None or entry.get("local_id") is None:
        raise HTTPException(404, "not on the watchlist (or not linked to a Stash entity yet)")
    for value in (body.from_date, body.until_date):
        if value and len(value) != 10:
            raise HTTPException(400, "dates must be YYYY-MM-DD")
    if body.from_date and body.until_date and body.from_date > body.until_date:
        raise HTTPException(400, "From must not be after Until")
    watchlist_repo.set_window_for_entity(_db, entity_type, entry["local_id"], body.from_date, body.until_date)
    window = _window(provider, entity_type, entity_stashdb_id)
    pairs = [(r["provider"], r["entity_type"], r["stashdb_id"]) for r in _entity_rows(entity_type, entry["local_id"])]
    outside = release_repo.open_releases_outside_window(_db, pairs, window["from"], window["until"])
    return {"window": window, "open_outside": outside}


@router.post("/{provider}/{entity_type}/{entity_stashdb_id}/dismiss-outside-window")
def dismiss_outside_window(provider: str, entity_type: str, entity_stashdb_id: str):
    window = _window(provider, entity_type, entity_stashdb_id)
    if window["local_id"] is None:
        raise HTTPException(404, "not on the watchlist")
    pairs = [(r["provider"], r["entity_type"], r["stashdb_id"]) for r in _entity_rows(entity_type, window["local_id"])]
    rows = []
    dismissed = release_repo.open_releases_outside_window(_db, pairs, window["from"], window["until"], dismiss=True, rows_out=rows)
    return {"dismissed": dismissed, "previous": rows}


@router.post("/{provider}/{entity_type}/{entity_stashdb_id}/fetch-year/{year}")
def fetch_year(provider: str, entity_type: str, entity_stashdb_id: str, year: int):
    """Fetches every scene dated in `year` for this one entity and
    stores it as status='backlog' (INSERT OR IGNORE -- a scene already
    tracked at any other status, e.g. already 'new' from the regular
    scan, is left untouched, see release_repo.insert_release()'s
    docstring). One year per call rather than a year range in one
    request -- the frontend calls this once per selected year, in
    sequence, which doubles as a simple progress indicator without
    needing a separate background-job/polling endpoint for what's
    normally a handful of bounded, single-entity fetches.

    Before storing, batch-checks which of this year's scenes are
    already in the local Stash library (2026-09-13, user's request to
    save bandwidth) and stores those *without* an image_url -- the
    browser then never fetches a thumbnail for a scene that's about to
    render as "In Library" anyway. Same check/mechanism as
    routers/releases.py's library_status() (which still runs too, at
    display time, to catch a scene that lands in the library *after*
    it was already fetched here with an image) -- this just also
    applies it at import time, so the image is never referenced at all
    for scenes that are already owned when the fetch happens, rather
    than stored-then-cleared a moment later."""
    _check_stashdb_only(provider, entity_type)
    client = _get_stashdb_client()
    scenes = list(client.fetch_scenes_for_year(entity_type, entity_stashdb_id, year))
    # Date window (2026-09-26): an edge year only keeps its in-window part.
    window = _window(provider, entity_type, entity_stashdb_id)
    total_seen = len(scenes)
    scenes = [s for s in scenes if watchlist_repo.in_window(s.get("date"), window["from"], window["until"])]

    if scenes:
        in_library = _get_stash_client().library_heights_by_stash_ids([s["id"] for s in scenes])
        for scene in scenes:
            if scene["id"] in in_library:
                scene["image_url"] = None

    with _db.connect() as conn:
        for scene in scenes:
            release_repo.insert_release(_db, conn, scene, provider, entity_type, entity_stashdb_id, status="backlog")
    backlog_repo.mark_year_loaded(_db, provider, entity_type, entity_stashdb_id, year)
    return {"year": year, "scenes_seen": total_seen, "scenes_stored": len(scenes),
            "outside_window": total_seen - len(scenes)}


PAIRING_STATUSES = ("backlog", "sent_to_whisparr", "import_failed")  # what the Missing from Catalog panel lists
PAIRING_BATCH = 200  # scenes per call (8 StashDB requests); the panel calls again until nothing remains


@router.post("/{provider}/{entity_type}/{entity_stashdb_id}/pairings")
def pairings(provider: str, entity_type: str, entity_stashdb_id: str):
    """Reads the cast (genders) of this entity's loaded scenes that have none stored yet and stores the scene pairing ('B/G', 'G/G' ...)
    on them (v0.182.0): rows loaded before the pairing was recorded. At most PAIRING_BATCH scenes per call; `remaining` tells the panel
    whether to call again. Only a cache of public StashDB data in our own table."""
    _check_stashdb_only(provider, entity_type)
    missing = release_repo.list_scenes_missing_pairing(_db, provider, entity_type, entity_stashdb_id, PAIRING_STATUSES)
    part = missing[:PAIRING_BATCH]
    if part:
        found = _get_stashdb_client().get_scenes_cast_batch(part)
        for sid in part:
            cast = found.get(sid)
            release_repo.set_pairing_for_scene(_db, provider, sid, release_repo.pairing_code(cast) if sid in found else "",
                                               release_repo.names_text(cast) if sid in found else "")
    return {"filled": len(part), "remaining": max(0, len(missing) - len(part))}


@router.get("/{provider}/{entity_type}/{entity_stashdb_id}/completeness")
def completeness(provider: str, entity_type: str, entity_stashdb_id: str, local_id: int):
    """"Missing from Catalog" completeness summary (2026-09-13, see
    PROGRESS.md's section of the same name): how many of this entity's
    total StashDB catalog scenes are already owned locally at (or
    above) the desired quality -- e.g. "152/160" -- so the panel can
    show a "Complete" state instead once everything's accounted for.

    `total` reuses the cached year overview (falls back to a live
    StashDB check the same way years() does, if nothing's cached yet
    for this entity -- no separate StashDB cost beyond what the year
    picker already needs). `in_library` is computed entirely against
    the LOCAL Stash library (stash_client.py's
    count_scenes_meeting_resolution(), using `local_id` -- this
    entity's own numeric Stash id, not its StashDB one, since that's
    what a local scene filter needs), so this never fetches the live
    remote catalog's actual scene list.

    Which quality profile (normal vs VR) applies to the whole entity
    is decided once via stashdb_client.py's is_vr_dominant() -- one
    extra cheap StashDB count query -- rather than per scene, since
    that would mean fetching the full catalog. Confirmed live this is
    exact for a real Studio (never genuinely mixed); for a Performer
    it's a majority call, not a guarantee -- see is_vr_dominant()'s
    docstring for the reasoning and the live studio/performer
    verification behind this tradeoff."""
    _check_stashdb_only(provider, entity_type)
    stashdb_client = _get_stashdb_client()

    overview = backlog_repo.get_year_overview(_db, provider, entity_type, entity_stashdb_id)
    if overview is None:
        counts = stashdb_client.count_scenes_by_year(entity_type, entity_stashdb_id)
        overview = backlog_repo.set_year_overview(_db, provider, entity_type, entity_stashdb_id, counts)
    total = sum(overview["years"].values())
    if total == 0:
        return {"total": 0, "in_library": 0, "complete": True}

    is_vr = stashdb_client.is_vr_dominant(entity_type, entity_stashdb_id)
    quality_profile_id, vr_quality_profile_id = resolve_whisparr_quality_profiles(_db)
    profile_id = vr_quality_profile_id if (is_vr and vr_quality_profile_id) else quality_profile_id

    whisparr_client = _get_whisparr_client()
    min_height = whisparr_client.get_quality_profile_cutoff_resolution(profile_id)

    stash_client = _get_stash_client()
    in_library = stash_client.count_scenes_meeting_resolution(entity_type, local_id, min_height)
    # provider scenes owned as ANOTHER title of the same video (scene_alias.py) count as owned too
    in_library += scene_alias.count_for_entity(_db, stash_client, entity_type, entity_stashdb_id, min_height)
    return {"total": total, "in_library": in_library, "complete": in_library >= total, "is_vr": is_vr}
