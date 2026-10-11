"""Keeps the watchlist in sync with Stash's own Favorite flag on
Studios/Performers -- the watchlist table (and its per-entity
watermark) still exists for tracking, but membership is no longer
edited by hand: starring/unstarring in Stash is the only way to
add/remove a watch. Run automatically right before every scan (see
PROGRESS.md's "Favorite-driven watchlist" section, decided 2026-09-11)
so the scan always reflects the current favorite set without a
separate manual step.

Since 2026-09-26 membership can alternatively follow a dedicated Stash
tag instead of the Favorite flag (`watchlist_source` = 'tag', see
watchlist_source.py) -- this module only asks watchlist_source for the
watched set, the rest is identical for both modes.

Extended 2026-09-11 (see PROGRESS.md's "ThePornDB scan integration"
section) to sync up to *two* watchlist rows per favorited entity, one
per provider it's been identified against in Stash -- StashDB (as
before) and now ThePornDB. An entity with only a StashDB match behaves
exactly as before; one with only a ThePornDB match is newly included
too (it wasn't before, since the old code required a StashDB id).

Generalized further 2026-09-21 (Metadata Provider generalization) --
StashDB no longer gets its own dedicated sync function, it now runs
through the exact same generic stash-box sync logic
(_sync_stashbox_provider()) that any additional provider from
metadata_provider_repo.py uses. Proven against this project's own real,
large (2000+ entity) live watchlist rather than only against a
hypothetical future second provider. ThePornDB keeps its own separate
_sync_theporndb() below unchanged -- its REST scan API needs a resolved
numeric native_id, not the raw stash-box UUID every other provider
(including StashDB) uses directly, a genuine protocol difference, not
a convenience special-case.
"""
import logging

import metadata_provider_repo
import watchlist_repo
import watchlist_source

logger = logging.getLogger(__name__)


def sync_watchlist_from_favorites(db, stash_client, theporndb_client):
    """Adds newly-watched studios/performers, removes ones no longer
    watched, for every configured provider independently. "Watched" =
    Favorite flag or the watchlist tag, depending on the
    `watchlist_source` setting (2026-09-26, see watchlist_source.py --
    the function keeps its old name, "favorites" is just the original
    mode). Raises WatchlistSourceError in tag mode when the tag is
    missing, instead of treating that as "nothing watched". Returns
    {"added": [...], "removed": [...], "resolve_failed": [...]}."""
    added = []
    removed = []
    resolve_failed = []

    generic_providers = ["stashdb"] + [p["key"] for p in metadata_provider_repo.list_all(db, enabled_only=True)]
    extra_endpoints = {p["key"]: p["url"] for p in metadata_provider_repo.list_all(db, enabled_only=True)}

    for entity_type in ("studio", "performer"):
        favorites = watchlist_source.fetch_watched(db, stash_client, entity_type, extra_endpoints)

        for provider in generic_providers:
            _sync_stashbox_provider(db, provider, entity_type, favorites, added, removed)
        _sync_theporndb(db, entity_type, favorites, theporndb_client, added, removed, resolve_failed)

    if added or removed:
        logger.info("watchlist sync: +%d -%d (%s)", len(added), len(removed),
                     {"added": added, "removed": removed})
    if resolve_failed:
        logger.warning("watchlist sync: %d ThePornDB id resolution failure(s): %s",
                        len(resolve_failed), resolve_failed)

    return {"added": added, "removed": removed, "resolve_failed": resolve_failed}


def _sync_stashbox_provider(db, provider, entity_type, favorites, added, removed):
    """Generic stash-box provider sync (2026-09-21) -- a plain stash-box
    GraphQL endpoint's own `stash_ids` UUID IS the provider-native id
    already, no resolution step needed (unlike ThePornDB below). Used
    for 'stashdb' and every additional provider from
    metadata_provider_repo.py.

    Calls add_to_watchlist() for *every* currently-favorited entity, not
    just newly-favorited ones (changed 2026-09-12, see PROGRESS.md's
    "Watchlist: merge duplicates, filter, link to Stash" section) --
    that's now an upsert, so this also keeps `local_id`/`name` fresh on
    already-existing rows (e.g. backfilling `local_id` for every row
    that predates that column, on this sync's very next run)."""
    favorite_ids = {f["provider_ids"].get(provider) for f in favorites if f["provider_ids"].get(provider)}
    current = watchlist_repo.list_watchlist(db, provider, entity_type)
    current_ids = {e["stashdb_id"] for e in current}

    for f in favorites:
        pid = f["provider_ids"].get(provider)
        if not pid:
            continue
        watchlist_repo.add_to_watchlist(
            db, provider, entity_type, pid, f["name"],
            local_id=int(f["id"]) if f.get("id") else None,
        )
        if pid not in current_ids:
            added.append({"provider": provider, "entity_type": entity_type, "name": f["name"]})

    for e in current:
        if e["stashdb_id"] not in favorite_ids:
            watchlist_repo.remove_from_watchlist(db, provider, entity_type, e["stashdb_id"])
            removed.append({"provider": provider, "entity_type": entity_type, "name": e["name"]})


def _sync_theporndb(db, entity_type, favorites, theporndb_client, added, removed, resolve_failed):
    """`stashdb_id` here means "this entity's id at ThePornDB's own
    stash-box-shim endpoint" (a UUID, see stash_client.py) -- used only
    to track watchlist membership, matching how Stash itself would show
    the match. The REST scan API needs a *different*, numeric id
    (`native_id`), resolved once via ThePornDBClient.resolve_native_id()
    when an entity is first added -- an existing row's native_id is never
    re-resolved (ThePornDB's numeric ids don't change)."""
    favorite_ids = {f["theporndb_id"] for f in favorites if f.get("theporndb_id")}
    current = watchlist_repo.list_watchlist(db, "theporndb", entity_type)
    current_ids = {e["stashdb_id"] for e in current}

    for f in favorites:
        uuid = f.get("theporndb_id")
        if not uuid:
            continue
        local_id = int(f["id"]) if f.get("id") else None
        if uuid in current_ids:
            # Already tracked -- just refresh local_id/name (see
            # add_to_watchlist()'s upsert), skip the network round-trip
            # to re-resolve native_id, it doesn't change once resolved.
            watchlist_repo.add_to_watchlist(db, "theporndb", entity_type, uuid, f["name"], local_id=local_id)
            continue
        try:
            native_id = theporndb_client.resolve_native_id(entity_type, uuid)
        except Exception as e:
            resolve_failed.append({"entity_type": entity_type, "name": f["name"], "error": str(e)})
            continue
        watchlist_repo.add_to_watchlist(
            db, "theporndb", entity_type, uuid, f["name"], native_id=native_id, local_id=local_id,
        )
        added.append({"provider": "theporndb", "entity_type": entity_type, "name": f["name"]})

    for e in current:
        if e["stashdb_id"] not in favorite_ids:
            watchlist_repo.remove_from_watchlist(db, "theporndb", entity_type, e["stashdb_id"])
            removed.append({"provider": "theporndb", "entity_type": entity_type, "name": e["name"]})
