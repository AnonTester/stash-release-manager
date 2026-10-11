"""One view of "what is this download job doing?" for every download client type (v0.135.0, torrent support stage 4).

The importers used to ask SABnzbd's history directly. `job_state()` answers the same question for SABnzbd and qBittorrent in one shape, so
the scene importer (download_sources.py), the movie importer (film_import.py) and the failure handling stay client-agnostic:

    {"state": "downloading" | "post_processing" | "completed" | "failed" | "missing",
     "name": job name, "storage": the finished output path in the CLIENT's own namespace (None until known),
     "fail_message": str | None,
     "keep_source": True for torrents (the files keep seeding: hard-linked or copied into the library, never moved/removed),
     "remote_root" / "local_mount": the client's own path mapping when it has one (qBittorrent: options remote_path / local_path, like the
       *arr "Remote Path Mappings"), else None -> the importer falls back to the SABnzbd downloads share setting.}

Like the *arr applications, a finished torrent counts as importable as soon as it has all its data (downloaded 100 %, no longer checking or
moving); seeding then goes on in the client."""
import logging

import qbittorrent_client
from qbittorrent_client import QbittorrentClient
from sabnzbd_client import SabnzbdClient

logger = logging.getLogger(__name__)


def torrent_client(client_row):
    return QbittorrentClient(client_row["url"], client_row["username"], client_row["password"], client_row["category"],
                             client_row.get("options"))


def job_state(client_row, job_id):
    """State of one job (see the module docstring). Raises when the client can't be reached (callers treat that as "try again later")."""
    if client_row is None or not job_id:
        return {"state": "missing"}
    if client_row["type"] == "sabnzbd":
        job = SabnzbdClient(client_row["url"], client_row["api_key"]).find_history_by_nzo_id(job_id)
        if job is None:
            return {"state": "downloading"}  # still downloading, or SABnzbd doesn't know the id (yet)
        if job.get("status") == "Failed":
            return {"state": "failed", "name": job.get("name"), "fail_message": job.get("fail_message"), "keep_source": False, "client": "SABnzbd"}
        if job.get("status") != "Completed":
            return {"state": "downloading", "name": job.get("name")}
        if not job.get("storage"):  # "Completed" comes before the output path is set (v0.125.3)
            return {"state": "post_processing", "name": job.get("name")}
        return {"state": "completed", "name": job.get("name"), "storage": job["storage"], "keep_source": False}
    if client_row["type"] == "qbittorrent":
        t = torrent_client(client_row).find_torrent(job_id)
        if t is None:
            return {"state": "missing"}  # removed from qBittorrent by hand (or a wrong hash)
        status = qbittorrent_client.status_of(t)
        if status == "error":
            return {"state": "failed", "name": t.get("name"), "fail_message": f"qBittorrent state: {t.get('state')}", "keep_source": True, "client": "qBittorrent"}
        if (t.get("progress") or 0) >= 1 and status in ("seeding", "done") and t.get("content_path"):
            opts = client_row.get("options") or {}
            return {"state": "completed", "name": t.get("name"), "storage": t["content_path"], "keep_source": True,
                    "remote_root": (opts.get("remote_path") or None), "local_mount": (opts.get("local_path") or None)}
        return {"state": "downloading", "name": t.get("name")}
    return {"state": "downloading"}  # a client type the importers don't read
