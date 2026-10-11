"""CRUD for configured download clients (Milestone 3, 2026-09-19) --
pluggable per Whisparr's own DownloadClientProvider model (see
PROGRESS.md's Milestone 3 planning research), collapsed to a single
`url` field rather than Whisparr's split host/port/useSsl, matching
this project's own existing connection-setting convention instead.
Replaces the old flat `sabnzbd_url`/`sabnzbd_api_key` settings -- see
db.py's `_migrate_seed_download_client_from_legacy_sabnzbd()` for the
one-shot upgrade path."""


import json

TYPE_PROTOCOL = {"sabnzbd": "usenet", "qbittorrent": "torrent"}


def _row(r):
    d = dict(r)
    try:
        d["options"] = json.loads(d.get("options") or "{}")
    except ValueError:
        d["options"] = {}
    return d


def list_all(db):
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM download_client ORDER BY priority ASC, id ASC").fetchall()
        return [_row(r) for r in rows]


def get_one(db, client_id):
    if client_id is None:
        return None
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM download_client WHERE id = ?", (client_id,)).fetchone()
        return _row(row) if row else None


def create(db, name, type_, protocol, priority, enabled, url, api_key, username, password, category, options=None):
    with db.connect() as conn:
        cur = conn.execute(
            """INSERT INTO download_client (name, type, protocol, priority, enabled, url, api_key, username, password, category, options)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, type_, protocol, priority, int(bool(enabled)), url, api_key, username, password, category, json.dumps(options or {})),
        )
        return cur.lastrowid


def update(db, client_id, **fields):
    if not fields:
        return
    if isinstance(fields.get("options"), dict):
        fields["options"] = json.dumps(fields["options"])
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db.connect() as conn:
        conn.execute(f"UPDATE download_client SET {cols} WHERE id = ?", (*fields.values(), client_id))


def delete(db, client_id):
    with db.connect() as conn:
        conn.execute("DELETE FROM download_client WHERE id = ?", (client_id,))


def pick_for_protocol(db, protocol):
    """Lowest-priority-number enabled client matching this protocol --
    mirrors Whisparr's own DownloadClientProvider routing (confirmed
    via its source, see PROGRESS.md's Milestone 3 research), minus the
    tag/round-robin nuance this project doesn't need at its current
    scale (one real client today)."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM download_client WHERE protocol = ? AND enabled = 1 ORDER BY priority ASC, id ASC LIMIT 1",
            (protocol,),
        ).fetchone()
        return _row(row) if row else None
