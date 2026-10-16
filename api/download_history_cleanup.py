"""Remove completed downloads from the download client's history (v0.215.0) -- what Sonarr / Radarr / Whisparr do after an import ("Remove
Completed"). Only SABnzbd jobs that are provably ours are touched: a finished history entry whose job id or release name matches a release /
movie of this program that is fully imported. Anything else in the history (other programs' downloads) is never looked at.

The duplicate guard used to read that history ("this release was downloaded before"). So before an entry is removed, its release name goes
into the ledger `downloaded_release`; the guard and the blocklist ask the ledger as well.
"""
import logging
import re

import download_client_repo
from sabnzbd_client import SabnzbdClient

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 5000


def norm(name):
    """Letters and digits only -- an indexer title and SABnzbd's job name differ in punctuation ("," becomes "_")."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def ledger_has(db, title):
    key = norm(title)
    if not key:
        return False
    with db.connect() as conn:
        return conn.execute("SELECT 1 FROM downloaded_release WHERE name_norm = ?", (key,)).fetchone() is not None


def ledger_title_for_job(db, nzo_id):
    if not nzo_id:
        return None
    with db.connect() as conn:
        row = conn.execute("SELECT title FROM downloaded_release WHERE nzo_id = ? LIMIT 1", (str(nzo_id),)).fetchone()
        return row["title"] if row else None


def _remember(db, title, nzo_id, client_id):
    with db.connect() as conn:
        conn.execute("""INSERT INTO downloaded_release (name_norm, title, nzo_id, client_id) VALUES (?, ?, ?, ?)
                        ON CONFLICT (name_norm) DO UPDATE SET nzo_id = excluded.nzo_id, client_id = excluded.client_id""",
                     (norm(title), title, str(nzo_id) if nzo_id else None, client_id))


def _imported_keys(db, client_id):
    """(job ids, normalized names) of everything that is imported for good: no verification pending, status 'imported'."""
    ids, names = set(), set()
    with db.connect() as conn:
        for table, extra in (("release", " AND pending_import_path IS NULL"), ("film_want", " AND pending_import_path IS NULL")):
            for r in conn.execute(f"SELECT download_client_id, download_job_id, grabbed_title FROM {table} WHERE status = 'imported'{extra}"):
                if r["download_job_id"] and r["download_client_id"] in (client_id, None):
                    ids.add(str(r["download_job_id"]))
                if r["grabbed_title"]:
                    names.add(norm(r["grabbed_title"]))
    return ids, names


def enabled_for(client_row):
    """The client option "remove completed" (default on, like the *arr applications); only SABnzbd for now."""
    return client_row.get("type") == "sabnzbd" and bool((client_row.get("options") or {}).get("remove_completed", True))


def find_candidates(db, client_row, client=None):
    """Finished history entries of this client that belong to a fully imported release of ours."""
    client = client or SabnzbdClient(client_row["url"], client_row["api_key"])
    ids, names = _imported_keys(db, client_row["id"])
    out = []
    for slot in client.get_history(limit=HISTORY_LIMIT):
        if (slot.get("status") or "").lower() != "completed":
            continue
        nzo_id, name = slot.get("nzo_id"), slot.get("name")
        if nzo_id and str(nzo_id) in ids:
            out.append({"nzo_id": nzo_id, "name": name, "matched_by": "job"})
        elif norm(name) and norm(name) in names:
            out.append({"nzo_id": nzo_id, "name": name, "matched_by": "name"})
    return out


def clean_client(db, client_row, dry_run=True, client=None):
    """Removes (or, in a dry run, only counts) the candidates of one client. The release name goes into the ledger BEFORE the entry is removed."""
    client = client or SabnzbdClient(client_row["url"], client_row["api_key"])
    found = find_candidates(db, client_row, client)
    if dry_run:
        return {"dry_run": True, "found": len(found), "sample": [c["name"] for c in found[:5]]}
    removed, errors = 0, []
    for c in found:
        try:
            _remember(db, c["name"], c["nzo_id"], client_row["id"])
            client.delete_history(c["nzo_id"])
            removed += 1
        except Exception as e:
            errors.append(f"{c['name']}: {e}")
            logger.warning("clean history: could not remove %r from the client's history: %s", c["name"], e)
    return {"dry_run": False, "found": len(found), "removed": removed, "errors": errors[:5]}


def clean_all(db):
    """The regular step (after a file-import run): every enabled SABnzbd client whose option is on. Never raises."""
    total = 0
    for row in download_client_repo.list_all(db):
        if not row.get("enabled") or not enabled_for(row):
            continue
        try:
            res = clean_client(db, row, dry_run=False)
            total += res["removed"]
        except Exception:
            logger.exception("clean history: client %s failed", row.get("name"))
    return total
