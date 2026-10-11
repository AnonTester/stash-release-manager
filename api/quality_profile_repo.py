"""CRUD for this project's own quality-profile concept (Milestone 3,
2026-09-19) -- deliberately much smaller than Whisparr's own (no custom
formats, no per-quality preferred-size target -- see PROGRESS.md's
Milestone 3 planning research): just an ordered list of allowed
resolutions (best first) plus a cutoff, matching only what
indexer_search.py's own selection logic needs today. Cutoff is stored
but not yet wired into the selection itself -- confirmed against
Whisparr's own source that cutoff only governs upgrade/stop-searching
decisions for an already-downloaded file, never which candidate a
fresh grab picks (see indexer_search.py's module docstring)."""
import json


def _deserialize(row):
    row = dict(row)
    row["resolutions"] = json.loads(row.pop("resolutions_json"))
    return row


def list_all(db):
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM quality_profile ORDER BY id ASC").fetchall()
        return [_deserialize(r) for r in rows]


def get_one(db, profile_id):
    if profile_id is None:
        return None
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM quality_profile WHERE id = ?", (profile_id,)).fetchone()
        return _deserialize(row) if row else None


def create(db, name, resolutions, cutoff_resolution):
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO quality_profile (name, resolutions_json, cutoff_resolution) VALUES (?, ?, ?)",
            (name, json.dumps(resolutions), cutoff_resolution),
        )
        return cur.lastrowid


def update(db, profile_id, name=None, resolutions=None, cutoff_resolution=None):
    fields = {}
    if name is not None:
        fields["name"] = name
    if resolutions is not None:
        fields["resolutions_json"] = json.dumps(resolutions)
    if cutoff_resolution is not None:
        fields["cutoff_resolution"] = cutoff_resolution
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db.connect() as conn:
        conn.execute(f"UPDATE quality_profile SET {cols} WHERE id = ?", (*fields.values(), profile_id))


def delete(db, profile_id):
    with db.connect() as conn:
        conn.execute("DELETE FROM quality_profile WHERE id = ?", (profile_id,))
