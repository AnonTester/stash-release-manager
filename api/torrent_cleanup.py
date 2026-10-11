"""Remove finished torrents once their seeding goal is reached (v0.135.0, torrent support stage 4) -- the *arr "Remove Completed" behavior.

Off by default (like the *arr applications): the setting `remove_completed_downloads`. When on, a torrent this sidecar grabbed and imported is removed
from the client, WITH its files (the library has its own hardlink/copy), as soon as qBittorrent itself reports the seeding goal as met: the torrent
is paused/stopped after having finished (qBittorrent's seed ratio / seed time limit with the action "Pause", also the per-torrent limits this sidecar
sets from an indexer's seed goals). A torrent that is still seeding, was not grabbed by this sidecar, or whose import is not finished is never touched.
(With the limit action "Remove" qBittorrent deletes the torrent by itself, nothing to do here.)"""
import logging

import download_client_repo
import download_jobs
import history
import qbittorrent_client

logger = logging.getLogger(__name__)


def _imported_hashes(db):
    with db.connect() as conn:
        rel = {r[0].lower() for r in conn.execute(
            "SELECT DISTINCT download_job_id FROM release WHERE status = 'imported' AND download_job_id IS NOT NULL")}
        films = {r[0].lower() for r in conn.execute(
            "SELECT DISTINCT download_job_id FROM film_want WHERE status = 'imported' AND download_job_id IS NOT NULL")}
    return rel | films


def run(db):
    """One pass over every enabled qBittorrent client. Returns {"removed": [names], "checked": n}."""
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM setting WHERE key = 'remove_completed_downloads'").fetchone()
    if not row or str(row["value"]).lower() != "true":
        return {"removed": [], "checked": 0, "enabled": False}
    imported = _imported_hashes(db)
    removed, checked = [], 0
    for client_row in download_client_repo.list_all(db):
        if client_row["type"] != "qbittorrent" or not client_row["enabled"]:
            continue
        try:
            client = download_jobs.torrent_client(client_row)
            for t in client.get_torrents():
                checked += 1
                if (t.get("hash") or "").lower() not in imported or (t.get("progress") or 0) < 1:
                    continue
                if t.get("state") not in qbittorrent_client.PAUSED:
                    continue  # still seeding (or checking): the goal is not met
                client.delete(t["hash"], delete_files=True)
                removed.append(t.get("name"))
                history.event("info", "download", f"Seeding goal reached, torrent removed from {client_row['name']}: {t.get('name')}")
        except Exception:
            logger.exception("torrent cleanup: client %s failed", client_row["name"])
    return {"removed": removed, "checked": checked, "enabled": True}
