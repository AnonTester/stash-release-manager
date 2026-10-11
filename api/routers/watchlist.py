import logging
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import inactive_check
import pending_favorite_decision_repo
import release_repo
import watchlist_repo
import watchlist_source

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/watchlist", tags=["watchlist"])

_db = None
_get_stash_client = None
_inactive_job = None  # main.py's InactiveCheckJob-like object: start(), status()


def init_watchlist_router(db, get_stash_client=None, inactive_job=None):
    global _db, _get_stash_client, _inactive_job
    _db = db
    _get_stash_client = get_stash_client
    _inactive_job = inactive_job


class WatchlistEntry(BaseModel):
    entity_type: str
    stashdb_id: str
    name: str
    provider: str = "stashdb"


class AutoHandoffUpdate(BaseModel):
    enabled: bool
    from_date: str | None = None
    until_date: str | None = None


class FavoriteChangedBody(BaseModel):
    entity_type: str
    local_id: int
    favorite: bool


class EntityChangedBody(BaseModel):
    entity_type: str
    local_id: int
    fields: list[str] = []


class WatchStateBody(BaseModel):
    watched: bool


class KeepBody(BaseModel):
    reasons: list[str] = []


class ResolveDecisionBody(BaseModel):
    action: str  # "keep" | "dismiss"


@router.get("")
def list_watchlist(provider: str | None = None, entity_type: str | None = None):
    if entity_type and entity_type not in ("studio", "performer"):
        raise HTTPException(400, "entity_type must be 'studio' or 'performer'")
    return watchlist_repo.list_watchlist(_db, provider, entity_type)


@router.post("")
def add_entry(entry: WatchlistEntry):
    """Unused by the plugin's own UI today (watchlist membership follows
    Stash's Favorite flag, see sync.py) -- kept for API completeness.
    `provider` defaults to 'stashdb' for backward compatibility."""
    if entry.entity_type not in ("studio", "performer"):
        raise HTTPException(400, "entity_type must be 'studio' or 'performer'")
    watchlist_repo.add_to_watchlist(_db, entry.provider, entry.entity_type, entry.stashdb_id, entry.name)
    return {"ok": True}


@router.delete("/{provider}/{entity_type}/{stashdb_id}")
def remove_entry(provider: str, entity_type: str, stashdb_id: str):
    if entity_type not in ("studio", "performer"):
        raise HTTPException(400, "entity_type must be 'studio' or 'performer'")
    watchlist_repo.remove_from_watchlist(_db, provider, entity_type, stashdb_id)
    return {"ok": True}


@router.post("/{provider}/{entity_type}/{stashdb_id}/auto-handoff")
def set_auto_handoff(provider: str, entity_type: str, stashdb_id: str, body: AutoHandoffUpdate):
    """StashDB only -- hand-off itself only ever works for StashDB-
    sourced releases (see handoff.py's send_to_whisparr() docstring), so
    a ThePornDB-provider watchlist row could never actually trigger
    one; rejecting it here keeps that constraint in one place instead
    of also needing to special-case it in watcher.py's dispatch."""
    if entity_type not in ("studio", "performer"):
        raise HTTPException(400, "entity_type must be 'studio' or 'performer'")
    if provider != "stashdb":
        raise HTTPException(400, "Auto hand-off only supports StashDB-sourced releases")
    watchlist_repo.set_auto_handoff(_db, provider, entity_type, stashdb_id,
                                     body.enabled, body.from_date, body.until_date)
    return {"ok": True}


@router.post("/favorite-changed")
def favorite_changed(body: FavoriteChangedBody):
    """Instant favorite-change detection (2026-09-21) -- called from
    stash_release_manager_backend.py's _handle_hook() (older plugin
    versions; current ones call /entity-changed below), itself triggered
    by Stash's own Performer.Update.Post/Studio.Update.Post plugin hook
    (see stash-release-manager.yml). Only meaningful while the watchlist
    follows Favorites -- in tag mode a Favorite change doesn't touch the
    watchlist, so it's ignored."""
    _check_entity_type(body.entity_type)
    if watchlist_source.get_mode(_db) != watchlist_source.MODE_FAVORITES:
        return {"ok": True, "ignored": "watchlist follows the tag, not Favorites"}
    return _on_watch_changed(body.entity_type, body.local_id, body.favorite)


@router.post("/entity-changed")
def entity_changed(body: EntityChangedBody):
    """Generic update hook (2026-09-26): a Studio/Performer was updated
    in Stash, `fields` = the hook's `inputFields`. Only acts when the
    field the current watchlist mode depends on was part of the update
    (`favorite` or `tag_ids`), and then reads the entity's CURRENT state
    from Stash rather than trusting the hook payload -- a bulk edit's
    input only describes the change (add/remove mode), not the result."""
    _check_entity_type(body.entity_type)
    relevant = "favorite" if watchlist_source.get_mode(_db) == watchlist_source.MODE_FAVORITES else "tag_ids"
    if relevant not in body.fields:
        return {"ok": True, "ignored": f"{relevant} not changed"}
    # Cheap exit before any Stash round-trip: an entity that isn't on the
    # watchlist can't need a decision on removal, and a newly watched one
    # is simply picked up by the next scan's sync.
    rows = watchlist_repo.find_by_local_id(_db, body.entity_type, body.local_id)
    if not rows:
        return {"ok": True, "matched": 0}
    watched, _ = watchlist_source.is_watched(_db, _get_stash_client(), body.entity_type, body.local_id)
    return _on_watch_changed(body.entity_type, body.local_id, watched)


def _check_entity_type(entity_type):
    if entity_type not in ("studio", "performer"):
        raise HTTPException(400, "entity_type must be 'studio' or 'performer'")


def _on_watch_changed(entity_type, local_id, watched):
    """Shared by both hook paths and the watchlist icon. Only knows the
    entity by Stash's own numeric id, hence find_by_local_id() rather
    than the usual (provider, stashdb_id) lookup.

    Watched again: clears any pending decision this entity might
    already have -- the user changed their mind before acting on the
    banner, nothing left to decide.

    No longer watched: flags a pending decision per provider row that
    still has open releases -- an entity with nothing open yet (e.g.
    never actually scanned) isn't worth interrupting the user about.
    Watchlist rows themselves change at the next scan's sync, as
    before."""
    rows = watchlist_repo.find_by_local_id(_db, entity_type, local_id)
    if not rows:
        return {"ok": True, "matched": 0}

    if watched:
        for row in rows:
            pending_favorite_decision_repo.resolve(_db, row["provider"], row["entity_type"], row["stashdb_id"])
        return {"ok": True, "matched": len(rows), "action": "cleared_pending"}

    flagged = []
    for row in rows:
        open_count = release_repo.count_open_for_entity(_db, row["provider"], row["entity_type"], row["stashdb_id"])
        if open_count > 0:
            pending_favorite_decision_repo.create_or_update(
                _db, row["provider"], row["entity_type"], row["stashdb_id"], row["name"], open_count,
            )
            flagged.append(row["provider"])
    return {"ok": True, "matched": len(rows), "flagged": flagged}


@router.get("/watch-state/{entity_type}/{local_id}")
def get_watch_state(entity_type: str, local_id: int):
    """Backs the watchlist icon next to Stash's own Favorite button on a
    Studio/Performer page (2026-09-26). `mode` tells the icon whether to
    show at all -- in favorites mode the Favorite button already IS the
    watchlist toggle."""
    _check_entity_type(entity_type)
    mode = watchlist_source.get_mode(_db)
    result = {"mode": mode, "tag_name": watchlist_source.get_tag_name(_db)}
    if mode != watchlist_source.MODE_TAG:
        return result
    watched, state = watchlist_source.is_watched(_db, _get_stash_client(), entity_type, local_id)
    return {**result, "watched": watched, "direct_tag": bool(state and state.get("direct_tag")),
            "found": state is not None}


@router.put("/watch-state/{entity_type}/{local_id}")
def set_watch_state(entity_type: str, local_id: int, body: WatchStateBody):
    _check_entity_type(entity_type)
    try:
        watchlist_source.set_watched(_db, _get_stash_client(), entity_type, local_id, body.watched)
    except watchlist_source.WatchlistSourceError as e:
        raise HTTPException(409, str(e))
    # The Update.Post hook fires too and ends up here as well -- harmless,
    # both steps are idempotent; doing it directly keeps the banner right
    # even if the hook isn't installed/enabled.
    decision = _on_watch_changed(entity_type, local_id, body.watched)
    return {**get_watch_state(entity_type, local_id), "decision": decision}


# --- "Probably inactive" review list (2026-09-26, see inactive_check.py) ---

def _inactive_months():
    from routers.settings import get_setting_value
    value = get_setting_value(_db, "inactive_after_months", "18")
    return int(value) if str(value).isdigit() else 18


@router.get("/inactive")
def list_inactive():
    return {
        "months": _inactive_months(),
        "items": inactive_check.list_review(_db, _inactive_months()),
        "last_run": inactive_check.last_run(_db),
        "job": _inactive_job.status() if _inactive_job else None,
        "mode": watchlist_source.get_mode(_db),
    }


@router.post("/inactive/run")
def run_inactive():
    if _inactive_job is None:
        raise HTTPException(503, "inactive check not available")
    return _inactive_job.start()


@router.post("/inactive/{entity_type}/{local_id}/keep")
def keep_inactive(entity_type: str, local_id: int, body: KeepBody):
    _check_entity_type(entity_type)
    inactive_check.keep(_db, entity_type, local_id, body.reasons)
    return {"ok": True}


# --- Migration favorites -> tag (2026-09-26) ---

_migration_lock = threading.Lock()
_migration_status = {"state": "idle"}


@router.get("/migrate-to-tag/status")
def migration_status():
    return _migration_status


@router.post("/migrate-to-tag")
def migrate_to_tag(dry_run: bool = False):
    """Puts the watchlist tag on every current Favorite Studio/Performer
    that doesn't carry it yet, verifies that every favorite is tagged,
    and only THEN switches `watchlist_source` to 'tag'. Watchlist rows
    stay exactly the same entities, so their scan watermarks survive (no
    full re-scan). Runs in the background (Stash's bulk update is ~0.75
    s/entity, so ~15 min for ~1250 entities) -- poll /status. If any
    batch fails or the check finds an untagged favorite, it stops
    WITHOUT switching; re-running it only tags what's still missing.

    `dry_run`: only counts, changes nothing (synchronous)."""
    stash_client = _get_stash_client()
    if dry_run:
        tag_id = watchlist_source.get_tag_id(_db, stash_client)
        plan = _migration_plan(stash_client, tag_id)
        return {"dry_run": True, "mode": watchlist_source.get_mode(_db),
                "tag_name": watchlist_source.get_tag_name(_db), "tag_exists": bool(tag_id),
                **{k: {"favorites": len(v["favorites"]), "to_tag": len(v["to_tag"])} for k, v in plan.items()}}
    if watchlist_source.get_mode(_db) == watchlist_source.MODE_TAG:
        raise HTTPException(409, "the watchlist already follows the tag")
    if not _migration_lock.acquire(blocking=False):
        raise HTTPException(409, "a migration is already running")
    _set_migration_status(state="running", phase="preparing", done=0, total=0, error=None)
    threading.Thread(target=_run_migration, args=(stash_client,), daemon=True).start()
    return _migration_status


def _set_migration_status(**fields):
    _migration_status.update(fields, updated_at=datetime.now(timezone.utc).isoformat())


def _migration_plan(stash_client, tag_id):
    plan = {}
    for entity_type in ("studio", "performer"):
        favorites = (stash_client.favorited_studios() if entity_type == "studio"
                     else stash_client.favorited_performers())
        tagged = set()
        if tag_id:
            fetch = stash_client.tagged_studios if entity_type == "studio" else stash_client.tagged_performers
            tagged = {e["id"] for e in fetch(tag_id)}
        plan[entity_type] = {"favorites": favorites, "to_tag": [f["id"] for f in favorites if f["id"] not in tagged]}
    return plan


def _run_migration(stash_client):
    try:
        tag_id = watchlist_source.get_tag_id(_db, stash_client, create=True)
        plan = _migration_plan(stash_client, tag_id)
        total = sum(len(p["to_tag"]) for p in plan.values())
        _set_migration_status(phase="tagging", total=total, done=0)
        failed_batches = 0
        batch_size = 25
        for entity_type, p in plan.items():
            ids = p["to_tag"]
            for i in range(0, len(ids), batch_size):
                _, failed = stash_client.update_entity_tags(entity_type, ids[i:i + batch_size], [tag_id], "ADD",
                                                            batch_size=batch_size)
                failed_batches += failed
                _set_migration_status(done=_migration_status["done"] + len(ids[i:i + batch_size]))
        _set_migration_status(phase="verifying")
        missing = {k: len(v["to_tag"]) for k, v in _migration_plan(stash_client, tag_id).items()}
        if failed_batches or any(missing.values()):
            _set_migration_status(
                state="failed", phase="stopped",
                error=(f"{failed_batches} batch(es) failed; still untagged: {missing['studio']} studio(s), "
                       f"{missing['performer']} performer(s). Nothing switched -- run the migration again."))
            return
        with _db.connect() as conn:
            conn.execute(
                """INSERT INTO setting (key, value) VALUES ('watchlist_source', 'tag')
                   ON CONFLICT (key) DO UPDATE SET value = excluded.value""")
        logger.info("watchlist migration: tagged %d entities with %r, watchlist now follows the tag",
                    total, watchlist_source.get_tag_name(_db))
        _set_migration_status(state="done", phase="switched")
    except Exception as e:
        logger.exception("watchlist migration failed")
        _set_migration_status(state="failed", phase="stopped", error=f"{type(e).__name__}: {e}")
    finally:
        _migration_lock.release()


@router.get("/pending-decisions")
def list_pending_decisions():
    return pending_favorite_decision_repo.list_all(_db)


@router.post("/pending-decisions/{provider}/{entity_type}/{stashdb_id}/resolve")
def resolve_pending_decision(provider: str, entity_type: str, stashdb_id: str, body: ResolveDecisionBody):
    if body.action not in ("keep", "dismiss"):
        raise HTTPException(400, "action must be 'keep' or 'dismiss'")
    previous, decision = None, None
    if body.action == "dismiss":
        decision = next((d for d in pending_favorite_decision_repo.list_all(_db)
                         if (d["provider"], d["entity_type"], d["stashdb_id"]) == (provider, entity_type, stashdb_id)), None)
        previous = release_repo.dismiss_all_open_for_entity(_db, provider, entity_type, stashdb_id)
    pending_favorite_decision_repo.resolve(_db, provider, entity_type, stashdb_id)
    return {"ok": True, "previous": previous, "decision": decision}


class ReopenDecisionBody(BaseModel):
    name: str = ""
    open_release_count: int = 0
    rows: list[dict] = []


@router.post("/pending-decisions/{provider}/{entity_type}/{stashdb_id}/reopen")
def reopen_pending_decision(provider: str, entity_type: str, stashdb_id: str, body: ReopenDecisionBody):
    """Undo of "Dismiss open releases" on the favorite-changed banner (v0.104.0): releases back to their old status, banner back."""
    restored = release_repo.restore_rows(_db, body.rows)
    pending_favorite_decision_repo.create_or_update(_db, provider, entity_type, stashdb_id, body.name, body.open_release_count)
    return {"ok": True, "restored": restored}
