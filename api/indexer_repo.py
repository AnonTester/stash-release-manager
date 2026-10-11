"""CRUD for this sidecar's own direct Newznab indexers (2026-09-25,
Indexers tab stage 2) -- same shape/conventions as
download_client_repo.py. See db.py's `indexer` table."""

_FIELDS = ("name", "type", "url", "api_path", "api_key", "categories", "priority", "enabled", "minimum_seeders", "seed_ratio", "seed_time")


def list_all(db):
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM indexer ORDER BY priority ASC, id ASC").fetchall()
        return [dict(r) for r in rows]


def list_enabled(db):
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM indexer WHERE enabled = 1 ORDER BY priority ASC, id ASC").fetchall()
        return [dict(r) for r in rows]


def get_one(db, indexer_id):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM indexer WHERE id = ?", (indexer_id,)).fetchone()
        return dict(row) if row else None


def create(db, **fields):
    values = {k: fields[k] for k in _FIELDS if k in fields}
    cols = ", ".join(values)
    with db.connect() as conn:
        cur = conn.execute(
            f"INSERT INTO indexer ({cols}) VALUES ({', '.join('?' * len(values))})",
            tuple(values.values()),
        )
        return cur.lastrowid


def update(db, indexer_id, **fields):
    values = {k: fields[k] for k in _FIELDS if k in fields}
    if not values:
        return
    cols = ", ".join(f"{k} = ?" for k in values)
    with db.connect() as conn:
        conn.execute(f"UPDATE indexer SET {cols} WHERE id = ?", (*values.values(), indexer_id))


def delete(db, indexer_id):
    with db.connect() as conn:
        conn.execute("DELETE FROM indexer WHERE id = ?", (indexer_id,))
