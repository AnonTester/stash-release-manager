"""CRUD for `pending_favorite_decision` (2026-09-21) -- see db.py's own
comment on that table. One row per (provider, entity) that was just
unfavorited in Stash with still-open releases, cleared once the user
picks Keep or Dismiss on the Releases page's banner (or if the entity
gets re-favorited before they decide -- see routers/watchlist.py's
favorite_changed())."""


def list_all(db):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_favorite_decision ORDER BY flagged_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def create_or_update(db, provider, entity_type, stashdb_id, name, open_release_count):
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO pending_favorite_decision
                   (provider, entity_type, stashdb_id, name, open_release_count)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (provider, entity_type, stashdb_id) DO UPDATE SET
                   name = excluded.name,
                   open_release_count = excluded.open_release_count,
                   flagged_at = datetime('now')""",
            (provider, entity_type, stashdb_id, name, open_release_count),
        )


def resolve(db, provider, entity_type, stashdb_id):
    with db.connect() as conn:
        conn.execute(
            "DELETE FROM pending_favorite_decision WHERE provider = ? AND entity_type = ? AND stashdb_id = ?",
            (provider, entity_type, stashdb_id),
        )
