"""Follow-up check: did a release handed off to Whisparr actually get
downloaded and imported? send_to_whisparr() is fire-and-forget by
design (the hand-off itself is synchronous and fast; the actual
indexer search/download/import happens on Whisparr's own time, from
seconds to hours later) -- this is the periodic/manual check that
closes the loop, added 2026-09-11 after manually verifying a real
hand-off's outcome by hand against Whisparr's API and realizing there
was no way to see that result from this project's own UI/DB.

Extended 2026-09-15 (milestone 1 of "get Whisparr out of the workflow",
see PROGRESS.md) to verify the file Whisparr reports against StashDB's
own registered fingerprints (see verification.py) before trusting
`movieFileId` at face value -- Whisparr has no way to know it grabbed a
mislabeled release (see that same date's "Boosting Morale"/"Summer
Camp Rules" investigation), so `movieFileId > 0` alone isn't proof this
project's own release actually arrived. A confirmed mismatch now marks
`import_failed` (a status this table's CHECK constraint has allowed
since 2026-09-11, but nothing ever actually set it until now) instead
of silently marking a wrong file as done.
"""
import logging

import release_repo
from verification import verify_downloaded_file

logger = logging.getLogger(__name__)


def check_import_status(db, whisparr_client, stash_client, stashdb_client):
    """For every release still marked sent_to_whisparr, looks up its
    current state in Whisparr by StashDB id. Once Whisparr reports a
    real file (movieFileId > 0), verifies it against StashDB's own
    fingerprints for that scene before trusting it:
    - Verified: marks 'imported'.
    - Confirmed mismatch (Whisparr has a file, but it's genuinely the
      wrong content): marks 'import_failed' instead.
    - Still unknown (Stash hasn't scanned it in yet, no fingerprints
      yet, etc.): left as 'sent_to_whisparr', same as before this
      verification existed -- next check tries again.
    Returns {"checked": n, "imported": [...], "import_failed": [...],
    "still_pending": n, "not_found": n}."""
    pending = release_repo.list_sent_to_whisparr(db)
    imported = []
    mismatched = []
    not_found = 0

    for release in pending:
        # Rows this sidecar's own pipeline is working on -- an upgrade (upgrade_of) or a file it moved into place
        # and is still verifying (pending_import_path) -- are NOT this check's business: Whisparr still reports
        # the OLD file as verified and used to flip them to 'imported' first (2026-09-30: five upgrades never
        # merged because a long Stash queue let this check win the race).
        if release.get("upgrade_of") or release.get("pending_import_path"):
            continue
        try:
            movie = whisparr_client.find_by_stash_id(release["scene_stashdb_id"])
        except Exception:
            logger.exception("import check failed for %s", release["scene_stashdb_id"])
            continue

        if movie is None:
            not_found += 1
            continue

        if not movie.get("movieFileId"):
            continue  # still genuinely pending, nothing to verify yet

        file_path = (movie.get("movieFile") or {}).get("path")
        verified, found_title = (
            verify_downloaded_file(stash_client, stashdb_client, release["scene_stashdb_id"], file_path)
            if file_path else (None, None)
        )

        if verified is False:
            release_repo.set_status(
                db, release["provider"], release["scene_stashdb_id"],
                release["entity_type"], release["entity_stashdb_id"], "import_failed",
            )
            mismatched.append({
                "scene_stashdb_id": release["scene_stashdb_id"], "title": release["title"],
                "path": file_path, "found_as": found_title,
            })
            logger.warning("import check: %r (scene %s) has a file in Whisparr but it doesn't match "
                            "StashDB's own fingerprints for this scene -- found in Stash as %r",
                            release["title"], release["scene_stashdb_id"], found_title)
            continue

        if verified is True:
            release_repo.set_status(
                db, release["provider"], release["scene_stashdb_id"],
                release["entity_type"], release["entity_stashdb_id"], "imported",
            )
            imported.append({
                "scene_stashdb_id": release["scene_stashdb_id"], "title": release["title"],
                "path": file_path,
            })
        # verified is None: still unknown, leave as sent_to_whisparr, try again next check

    summary = {
        "checked": len(pending), "imported": imported, "import_failed": mismatched,
        "still_pending": len(pending) - len(imported) - len(mismatched), "not_found": not_found,
    }
    if imported:
        logger.info("import check: %d of %d pending release(s) now imported: %s",
                     len(imported), len(pending), imported)
    if mismatched:
        logger.warning("import check: %d of %d pending release(s) had the WRONG file: %s",
                        len(mismatched), len(pending), mismatched)
    return summary
