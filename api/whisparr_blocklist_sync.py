"""Pulls Whisparr's own blocklist into this sidecar's own local
`release.review_rejected_titles` (2026-09-23) -- see
project_roadmap_todo.md memory's "Import-failed reason + conditional
Retry" entry for the full design discussion.

Why this exists as a periodic PULL, not a live per-search lookup: this
project's whole reason for existing is to eventually replace Whisparr
entirely (see the "wir wollen Whisparr eigentlich ganz ersetzen" note
elsewhere in that memory). A user may accumulate real blocklist
history IN Whisparr over a long period under one of the Whisparr-
driven scope presets, then eventually decommission Whisparr once a
fully-standalone preset exists -- a live query at search time fails
that scenario entirely, since by the time Whisparr is gone it's too
late to ask it anything. The knowledge has to already be copied into
this sidecar's own DB before that happens, which means it has to
happen continuously, on a schedule, while Whisparr is still around --
not read on demand.

Also checked live 2026-09-23 whether a Whisparr webhook could replace
polling here: `GET /api/v3/notification/schema`'s Webhook
implementation only supports onGrab/onDownload/onUpgrade/onRename/
onMovieAdded/onMovieDelete/onMovieFileDelete/
onMovieFileDeleteForUpgrade/onHealthIssue/onHealthRestored/
onApplicationUpdate/onManualInteractionRequired -- no blocklist-add
event exists at all, for either an automatic or a manual (via
Whisparr's own UI) blocklist action. Unlike Stash (Performer.Update.Post/
Studio.Update.Post, see v0.46.0), Whisparr genuinely offers no hook for
this -- polling is the only available mechanism, not a shortcut taken
for convenience.
"""
import logging

import release_repo

logger = logging.getLogger(__name__)


def sync_whisparr_blocklist(db, whisparr_client):
    """Pulls Whisparr's ENTIRE blocklist (whisparr_client.get_blocklist(),
    not the per-movie endpoint -- this project wants everything) and
    writes each entry's own `sourceTitle` into
    release_repo.add_review_rejected_title() for the matching StashDB
    scene -- the SAME write path file_importer.fix_wrong_download()
    uses after a sidecar-detected wrong grab, so `_select_best()`
    (indexer_search.py) excludes it regardless of which system
    originally decided it was bad. `movieId` -> StashDB scene id is
    resolved via whisparr_client.get_movie(), cached per sync run since
    more than one blocklist entry can belong to the same movie.

    Best-effort per entry -- one movie lookup failing (a stale
    `movieId` for a since-deleted movie, a transient error) logs and
    skips just that entry rather than failing the whole sync. Returns a
    summary dict."""
    entries = whisparr_client.get_blocklist()
    movie_stash_id_cache = {}
    synced, skipped, errors = 0, 0, 0

    for entry in entries:
        movie_id = entry.get("movieId")
        source_title = entry.get("sourceTitle")
        if not movie_id or not source_title:
            skipped += 1
            continue

        if movie_id not in movie_stash_id_cache:
            try:
                movie = whisparr_client.get_movie(movie_id)
                movie_stash_id_cache[movie_id] = movie.get("stashId")
            except Exception:
                logger.exception("whisparr_blocklist_sync: couldn't resolve movie %s, skipping its blocklist entry",
                                  movie_id)
                movie_stash_id_cache[movie_id] = None
                errors += 1
                continue

        stash_id = movie_stash_id_cache[movie_id]
        if not stash_id:
            skipped += 1
            continue

        release_repo.add_review_rejected_title(db, "stashdb", stash_id, source_title)
        synced += 1

    summary = {"total": len(entries), "synced": synced, "skipped": skipped, "errors": errors}
    logger.info("whisparr_blocklist_sync: %s", summary)
    return summary
