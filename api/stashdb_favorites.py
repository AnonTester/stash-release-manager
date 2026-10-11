"""Imports StashDB.org's own `is_favorite` flag (the user's account-level
favorites on stashdb.org itself, via PerformerQueryInput/StudioQueryInput's
`is_favorite`) into this Stash instance's local `favorite` flag -- one
directional, StashDB -> local Stash, and only for entities that already
exist locally (nothing is created).

This is a separate concern from api/sync.py's watchlist sync: that step
still reads *local* Stash favorites to build the watchlist (unchanged).
This module is what keeps local favorites honest with what the user
actually curates on StashDB.org, since they don't use this Stash
instance's own heart icon much -- see PROGRESS.md's "StashDB.org
favorites import" section for the conversation that led here.
"""
import logging

logger = logging.getLogger(__name__)


def import_stashdb_favorites(stashdb_client, stash_client, endpoint=None):
    """Returns a summary dict -- "matched" is how many StashDB favorites
    correspond to a locally-known entity at all; "batches_ok"/
    "batches_failed" reflect set_studios_favorite()/set_performers_favorite()'s
    per-batch error handling (see stash_client.py's _bulk_favorite()) --
    a nonzero failed count means some ids likely didn't get favorited
    this run and a re-run will retry them (safe, idempotent) alongside
    everything else.

    Only sends the bulk-favorite mutation for ids that AREN'T already
    locally favorited (2026-09-13) -- previously ran on every matched
    id unconditionally every single time, on the (locally true)
    assumption from stash_client.py's _bulk_favorite() docstring that
    "a bulk update on an already-favorite id is a harmless no-op." That
    reasoning only accounted for Stash's own favorite flag -- it missed
    that the mutation still fires Stash's `Performer.Update.Post` hook
    for every id regardless of whether anything actually changed.
    Found live: the "stashdb performer gallery" plugin listens on
    exactly that hook and queues its own "Process Performers" task per
    performer -- with ~667 favorited performers, every sync cycle (and,
    before 0.20.5's fix, every container restart during this session's
    own development) was silently queueing another ~667 of those tasks
    even when nothing had changed, which piled up into a 9947-deep
    backlog blocking Stash's entire (single-threaded) job queue,
    including a real Whisparr-triggered library scan behind it. Skips
    the mutation call entirely (and its own Stash-side cost, ~0.75s/id)
    for anything already favorited, not just the plugin-hook side
    effect."""
    favorite_studio_ids = set(stashdb_client.favorited_studio_ids())
    favorite_performer_ids = set(stashdb_client.favorited_performer_ids())

    local_studios = stash_client.local_studio_ids_by_stashdb_id(endpoint)
    local_performers = stash_client.local_performer_ids_by_stashdb_id(endpoint)

    matched_studio_local_ids = [local_studios[sid] for sid in favorite_studio_ids if sid in local_studios]
    matched_performer_local_ids = [local_performers[sid] for sid in favorite_performer_ids if sid in local_performers]

    already_favorite_studio_ids = {int(s["id"]) for s in stash_client.favorited_studios()}
    already_favorite_performer_ids = {int(p["id"]) for p in stash_client.favorited_performers()}
    new_studio_ids = [i for i in matched_studio_local_ids if i not in already_favorite_studio_ids]
    new_performer_ids = [i for i in matched_performer_local_ids if i not in already_favorite_performer_ids]

    studio_ok, studio_failed = stash_client.set_studios_favorite(new_studio_ids)
    performer_ok, performer_failed = stash_client.set_performers_favorite(new_performer_ids)

    summary = {
        "studios_matched": len(matched_studio_local_ids),
        "performers_matched": len(matched_performer_local_ids),
        "studios_favorited": len(new_studio_ids),
        "performers_favorited": len(new_performer_ids),
        "studio_batches_ok": studio_ok, "studio_batches_failed": studio_failed,
        "performer_batches_ok": performer_ok, "performer_batches_failed": performer_failed,
    }
    logger.info("StashDB favorites import: %s (StashDB had %d favorite studio(s), %d favorite performer(s) total)",
                summary, len(favorite_studio_ids), len(favorite_performer_ids))
    return summary
