"""Where to find a completed-but-not-yet-imported download -- the
pluggable seam file_importer.py's pipeline is built against
(2026-09-17, see PROGRESS.md's "File Handling" section).

Two implementations, selected by the `file_import_source` setting (see
routers/settings.py's DEFAULTS and main.py's run_file_import()):

- `WhisparrQueueSource` (the original, Milestone 2 design): reads
  Whisparr's own `/queue` for `trackedDownloadState ==
  "importPending"`. **Found broken 2026-09-19** (see the Milestone 2
  cutover investigation in PROGRESS.md): with
  `enableCompletedDownloadHandling` off, Whisparr's `/queue` only ever
  mirrors the download client's live active queue -- once a download
  finishes and the download client moves it into its own history,
  Whisparr drops it without ever producing an `importPending` entry.
  Kept only for the Settings page's source switch, for a user who
  wants Whisparr's own built-in import instead (in which case
  `enableCompletedDownloadHandling` should be back ON and this source
  effectively never has anything to do).
- `SabnzbdSource` (2026-09-19 fix, effectively Milestone 3's SABnzbd
  hook brought forward; rewritten again 2026-09-19 for Milestone 3
  proper): bypasses Whisparr's queue entirely. Originally matched a
  release to its SABnzbd job via the `downloadId` Whisparr recorded on
  its own `grabbed` history event -- now that indexer_search.py grabs
  directly (Whisparr never does the grab itself, see handoff.py), there
  is no such Whisparr history event to read anymore. Reads
  `release.download_client_id`/`download_job_id` instead (set by
  indexer_search.py at grab time) and resolves the matching
  download_client row itself, rather than needing a whisparr_client at
  all.
"""
import logging

import download_client_repo
import release_repo
import download_jobs
from sabnzbd_client import SabnzbdClient

logger = logging.getLogger(__name__)


class WhisparrQueueSource:
    def __init__(self, whisparr_client):
        self.whisparr_client = whisparr_client

    def list_completed_downloads(self):
        """Every queue item Whisparr itself has finished downloading
        but -- with its own auto-import disabled -- is NOT going to
        import on its own. Returns a list of
        {"stash_id", "movie_id", "queue_id", "output_path"} dicts, the
        shape file_importer.py's import_one() expects regardless of
        which source produced it.

        Skips anything not in `importPending`: `downloading` isn't
        finished yet, `importBlocked`/`failed`/`ignored` need human
        attention (a name Whisparr itself couldn't parse, a release it
        gave up on, etc.) rather than a silent best-effort import
        attempt, and `importing`/`imported` mean something already
        claimed it (shouldn't happen with completed-download-handling
        off, but skip rather than risk a double-import if it does)."""
        items = []
        for entry in self.whisparr_client.get_queue():
            if entry.get("trackedDownloadState") != "importPending":
                continue
            output_path = entry.get("outputPath")
            movie_id = entry.get("movieId")
            if not output_path or not movie_id:
                continue
            movie = self.whisparr_client.get_movie(movie_id)
            stash_id = movie.get("stashId")
            if not stash_id:
                continue
            items.append({
                "stash_id": stash_id,
                "movie_id": movie_id,
                "queue_id": entry["id"],
                "output_path": output_path,
            })
        return items


class SabnzbdSource:
    def __init__(self, db):
        self.db = db

    def list_completed_downloads(self):
        """Every `sent_to_whisparr` release with a recorded download
        job (`release_repo.list_grabbed_awaiting_import()`, already
        deduped by scene) whose matching SABnzbd job has actually
        finished. `queue_id` is always None here -- there is no
        Whisparr queue entry to clear once imported (see
        file_importer.py's run_file_import(), which skips
        clear_queue_item() when it's falsy). `output_path` is SABnzbd's
        own `storage` path, in SABnzbd's own container/host namespace
        (translated by file_importer.py via the `sabnzbd_downloads_root`
        /`local_downloads_mount` settings)."""
        items = []
        for release in release_repo.list_grabbed_awaiting_import(self.db):
            client_row = download_client_repo.get_one(self.db, release["download_client_id"])
            if client_row is None or client_row["type"] not in ("sabnzbd", "qbittorrent"):
                continue  # client since removed/reconfigured, or a client type this source doesn't read yet
            try:
                job = download_jobs.job_state(client_row, release["download_job_id"])
            except Exception:
                logger.exception("download source: could not ask %s about job %s", client_row["name"], release["download_job_id"])
                continue
            if job["state"] == "failed":
                # v0.94.2: a failed job used to wait for ever ("waiting for import") -- blocklist it, back to searching
                import download_failures
                download_failures.handle_failed(self.db, release, job)
                continue
            if download_jobs.is_lost(job, release.get("grabbed_at")):
                import download_failures
                download_failures.handle_lost(self.db, release)
                continue
            if job["state"] != "completed":
                continue  # still downloading / in post-processing (the next run gets it) / a torrent removed by hand
            items.append({
                "stash_id": release["scene_stashdb_id"],
                "provider": release["provider"],  # 2026-09-25: not only StashDB any more
                "queue_id": None,
                "output_path": job["storage"],
                # torrents: the files keep seeding (hard-linked or copied, never moved) and sit in the client's own path namespace
                "keep_source": job.get("keep_source", False),
                "remote_root": job.get("remote_root"), "local_mount": job.get("local_mount"),
            })
        return items
