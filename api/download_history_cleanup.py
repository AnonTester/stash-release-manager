"""Remove completed downloads from the download client's history (v0.215.0) -- what Sonarr / Radarr / Whisparr do after an import ("Remove
Completed"). Only SABnzbd jobs that are provably ours are touched: a finished history entry whose job id or release name matches a release /
movie of this program that is fully imported. Anything else in the history (other programs' downloads) is never looked at.

The duplicate guard used to read that history ("this release was downloaded before"). So before an entry is removed, its release name goes
into the ledger `downloaded_release`; the guard and the blocklist ask the ledger as well.
"""
import json
import logging
import re

import download_client_repo
from sabnzbd_client import SabnzbdClient

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 5000
MIN_BLOCK_LEN = 20  # a shorter name is too unspecific to match by its start


def norm(name):
    """Letters and digits only -- an indexer title and SABnzbd's job name differ in punctuation ("," becomes "_")."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


_EXT_RE = re.compile(r"(\.(par2|nfo|nzb|sfv|rar|r\d{2}|mkv|mp4|avi|wmv|m4v|iso))+$", re.I)


def norm_title(name):
    """`norm` of a grabbed title: a movie's grabbed_title is the first FILE name of the NZB ("... .par2"), the client's job name has no extension."""
    return norm(_EXT_RE.sub("", name or ""))


def ledger_has(db, title):
    """True when this release name was downloaded AND imported before (its history entry was removed): no scene may fetch it again. Names that were
    only rejected / blocklisted do not count -- that is the blocklist of the scene they were rejected for (v0.218.1)."""
    key = norm(title)
    if not key:
        return False
    with db.connect() as conn:
        return conn.execute("SELECT 1 FROM downloaded_release WHERE name_norm = ? AND outcome = 'imported'", (key,)).fetchone() is not None


def rejected_only(db, title):
    """True when this name is on some scene's blocklist and was never imported for any scene: a SABnzbd history entry of it is no reason to refuse
    it for ANOTHER scene (v0.218.1)."""
    key = norm_title(title)
    if not key or len(key) < MIN_BLOCK_LEN:
        return False
    _ids, names = _imported_keys(db, None)
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM downloaded_release WHERE name_norm = ? AND outcome = 'imported'", (norm(title),)).fetchone():
            return False
    return key not in names and _is_blocked(key, _blocked_names(db))


def backfill_outcome(conn):
    """One-time: ledger rows written before `outcome` existed -- 'rejected' for names only known from a blocklist, never as imported."""
    names = set()
    for table in ("release", "film_want"):
        for r in conn.execute(f"SELECT grabbed_title FROM {table} WHERE status = 'imported' AND grabbed_title IS NOT NULL"):
            names.add(norm_title(r[0]))
    blocked = _blocked_names_conn(conn)
    for r in conn.execute("SELECT name_norm, title FROM downloaded_release").fetchall():
        key = norm_title(r[1])
        if key not in names and _is_blocked(key, blocked):
            conn.execute("UPDATE downloaded_release SET outcome = 'rejected' WHERE name_norm = ?", (r[0],))


def ledger_title_for_job(db, nzo_id):
    if not nzo_id:
        return None
    with db.connect() as conn:
        row = conn.execute("SELECT title FROM downloaded_release WHERE nzo_id = ? LIMIT 1", (str(nzo_id),)).fetchone()
        return row["title"] if row else None


def _remember(db, title, nzo_id, client_id, outcome="imported"):
    with db.connect() as conn:
        conn.execute("""INSERT INTO downloaded_release (name_norm, title, nzo_id, client_id, outcome) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT (name_norm) DO UPDATE SET nzo_id = excluded.nzo_id, client_id = excluded.client_id,
                          outcome = CASE WHEN downloaded_release.outcome = 'imported' THEN 'imported' ELSE excluded.outcome END""",
                     (norm(title), title, str(nzo_id) if nzo_id else None, client_id, outcome))


def _imported_keys(db, client_id):
    """(job ids, normalized names) of everything that is imported for good: no verification pending, status 'imported'."""
    ids, names = set(), set()
    with db.connect() as conn:
        for table, extra in (("release", " AND pending_import_path IS NULL"), ("film_want", " AND pending_import_path IS NULL")):
            for r in conn.execute(f"SELECT download_client_id, download_job_id, grabbed_title FROM {table} WHERE status = 'imported'{extra}"):
                if r["download_job_id"] and r["download_client_id"] in (client_id, None):
                    ids.add(str(r["download_job_id"]))
                if r["grabbed_title"]:
                    names.add(norm_title(r["grabbed_title"]))
    return ids, names


def _active_job_ids(db):
    """Job ids of downloads whose import is still open (waiting, being encoded, failed but kept): their history entry is how the importer finds the
    file. Found 2026-10-17: a release picked by hand from the manual search although it was blocklisted matched the 'blocked' rule and its
    entry was removed while the import was pending -- the scene then waited for ever."""
    ids = set()
    with db.connect() as conn:
        for table, statuses in (("release", ("sent_to_whisparr", "import_failed")), ("film_want", ("grabbed", "import_failed"))):
            marks = ",".join("?" * len(statuses))
            for r in conn.execute(f"SELECT download_job_id FROM {table} WHERE download_job_id IS NOT NULL AND status IN ({marks})", statuses):
                ids.add(str(r[0]))
    return ids


def _blocked_names(db):
    """Release names this program rejected itself (blocklisted or quality-rejected downloads, of scenes and movies): the download is over for good, its
    file was discarded, the name stays blocked -- the history entry has nothing left to say."""
    with db.connect() as conn:
        return _blocked_names_conn(conn)


def _blocked_names_conn(conn):
    out = set()
    for table, col in (("release", "review_rejected_titles"), ("film_want", "rejected_titles")):
        for r in conn.execute(f"SELECT DISTINCT {col} AS v FROM {table} WHERE {col} IS NOT NULL AND {col} != ''"):
            try:
                for t in json.loads(r[0]):
                    k = norm_title(str(t).removeprefix("exact:"))
                    if len(k) >= MIN_BLOCK_LEN:
                        out.add(k)
            except (TypeError, ValueError):
                continue
    return out


def _is_blocked(key, blocked):
    """Same name, or one is the start of the other (a blocked "...1080p" also covers "...1080P.x265", the other encode of that release)."""
    return key in blocked or any(key.startswith(b) or b.startswith(key) for b in blocked if len(key) >= MIN_BLOCK_LEN)


def enabled_for(client_row):
    """The client option "remove completed" (default on, like the *arr applications); only SABnzbd for now."""
    return client_row.get("type") == "sabnzbd" and bool((client_row.get("options") or {}).get("remove_completed", True))


def find_candidates(db, client_row, client=None):
    """Finished history entries of this client that belong to a fully imported release of ours, or to a download this program rejected / blocklisted."""
    client = client or SabnzbdClient(client_row["url"], client_row["api_key"])
    ids, names = _imported_keys(db, client_row["id"])
    blocked = _blocked_names(db)
    active = _active_job_ids(db)
    out = []
    for slot in client.get_history(limit=HISTORY_LIMIT):
        if (slot.get("status") or "").lower() != "completed":
            continue
        nzo_id, name = slot.get("nzo_id"), slot.get("name")
        if nzo_id and str(nzo_id) in active and str(nzo_id) not in ids:
            continue  # its import is still open
        if nzo_id and str(nzo_id) in ids:
            out.append({"nzo_id": nzo_id, "name": name, "matched_by": "job"})
        elif norm_title(name) and norm_title(name) in names:
            out.append({"nzo_id": nzo_id, "name": name, "matched_by": "name"})
        elif norm_title(name) and _is_blocked(norm_title(name), blocked) and '"ours":false' not in (slot.get("script_line") or ""):
            # (the post-processing script's note "ours": false = a download this program did not start: never ours to remove)
            out.append({"nzo_id": nzo_id, "name": name, "matched_by": "blocked"})
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
            _remember(db, c["name"], c["nzo_id"], client_row["id"], "rejected" if c["matched_by"] == "blocked" else "imported")
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


# ---- the SABnzbd Archive (v0.222.3) ----
# SABnzbd 4.2+ keeps a deleted history entry in its Archive, and its duplicate detection reads the archive too ("Duplicate NZB" for a release
# that was downloaded long ago). This program removes its own entries for real (sabnzbd_client.delete_history, archive=0), so the archive only
# fills with what the user or other programs delete. A regular purge keeps it from blocking a re-grab months later.
ARCHIVE_PURGE_DEFAULT_DAYS = 30


def purge_archive(client_row, older_than_days=ARCHIVE_PURGE_DEFAULT_DAYS, dry_run=True, client=None, now=None):
    """Removes the Archive entries of this client that are older than `older_than_days` (by their completed time) and, when the client row names a
    category, belong to it -- other categories (other programs) stay. Dry run (default): only counts. After a real run the archive is read again:
    when nothing went away SABnzbd ignored the call and the run says so (`warning`)."""
    import time
    client = client or SabnzbdClient(client_row["url"], client_row["api_key"])
    cutoff = (now if now is not None else time.time()) - int(older_than_days) * 86400
    category = (client_row.get("category") or "").strip().lower()
    old = [e for e in client.get_archive()
           if (e.get("completed") or 0) and int(e["completed"]) < cutoff and e.get("nzo_id")
           and (not category or (e.get("category") or "").lower() == category)]
    out = {"dry_run": dry_run, "found": len(old), "older_than_days": int(older_than_days), "sample": [e.get("name") for e in old[:5]]}
    if dry_run or not old:
        return out
    removed, errors = 0, []
    for e in old:
        try:
            client.delete_archived(e["nzo_id"])
            removed += 1
        except Exception as ex:  # noqa: BLE001
            errors.append(f"{e.get('name')}: {ex}")
    out.update(removed=removed, errors=errors[:5])
    left = {e.get("nzo_id") for e in client.get_archive()}
    if removed and all(e["nzo_id"] in left for e in old):
        out["warning"] = "SABnzbd still lists every entry in its Archive -- it did not remove them (purge them in SABnzbd by hand)"
        logger.warning("archive purge: %s", out["warning"])
    return out


def purge_archives(db, older_than_days):
    """The regular step: every enabled SABnzbd client whose option is on. Never raises; returns the number of removed entries."""
    total = 0
    for row in download_client_repo.list_all(db):
        if not row.get("enabled") or not enabled_for(row):
            continue
        try:
            res = purge_archive(row, older_than_days, dry_run=False)
            total += res.get("removed", 0)
            if res.get("removed"):
                logger.info("archive purge: %d entr(ies) older than %d days removed from %s", res["removed"], older_than_days, row.get("name"))
        except Exception:
            logger.exception("archive purge: client %s failed", row.get("name"))
    return total
