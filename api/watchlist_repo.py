"""CRUD for the watchlist and its per-(provider, entity) watermarks."""


def list_watchlist(db, provider=None, entity_type=None):
    query = "SELECT * FROM watchlist WHERE 1=1"
    params = []
    if provider:
        query += " AND provider = ?"
        params.append(provider)
    if entity_type:
        query += " AND entity_type = ?"
        params.append(entity_type)
    query += " ORDER BY provider, entity_type, name"
    with db.connect() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def find_by_local_id(db, entity_type, local_id):
    """Every watchlist row (one per provider that entity is favorited
    against, e.g. both stashdb and theporndb) for a given LOCAL Stash
    entity id -- used by the favorite-changed hook handler
    (routers/watchlist.py's favorite_changed()), which only ever knows
    the entity by Stash's own numeric id (that's all a Performer.Update.Post/
    Studio.Update.Post hook payload carries, see stash_release_manager_backend.py's
    _handle_hook())."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM watchlist WHERE entity_type = ? AND local_id = ?",
            (entity_type, local_id),
        ).fetchall()
        return [dict(r) for r in rows]


def find_others_by_name(db, exclude_provider, entity_type, name):
    """Every watchlist row for the same real-world entity at a
    *different* provider -- every provider's row for one Stash entity is
    added from the same favorited-entity record (see sync.py) and so
    always shares the exact same `name`, making this an exact, non-fuzzy
    link (unlike the actual scene-dedup match in release_repo.py, which
    has no such shared id to rely on). Used for cross-provider release
    dedup, see PROGRESS.md's "Cross-provider dedup" section.

    Generalized 2026-09-21 from a single-sibling find_by_name() (a
    hardcoded StashDB<->ThePornDB binary toggle only ever needed exactly
    one) to return every OTHER configured provider's row, now that a
    real-world entity can be watched under more than two providers at
    once."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM watchlist WHERE provider != ? AND entity_type = ? AND name = ?",
            (exclude_provider, entity_type, name),
        ).fetchall()
        return [dict(r) for r in rows]


def add_to_watchlist(db, provider, entity_type, stashdb_id, name, native_id=None, local_id=None):
    """Upsert, not insert-or-ignore -- sync.py now calls this for every
    *currently* favorited entity each sync, not just newly-favorited
    ones (see PROGRESS.md's "Watchlist: merge duplicates, filter, link
    to Stash" section), so an already-existing row's `local_id` (and a
    possibly-changed `name`) get refreshed too instead of staying stuck
    at whatever they were on first insert. `native_id`/`local_id` are
    only overwritten when a non-None value is passed -- COALESCE keeps
    an already-resolved value if this particular call doesn't have one
    (e.g. sync.py's ThePornDB branch resolves native_id once and never
    re-passes it on later calls)."""
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO watchlist (provider, entity_type, stashdb_id, native_id, local_id, name)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (provider, entity_type, stashdb_id) DO UPDATE SET
                   native_id = COALESCE(excluded.native_id, watchlist.native_id),
                   local_id = COALESCE(excluded.local_id, watchlist.local_id),
                   name = excluded.name""",
            (provider, entity_type, stashdb_id, native_id, local_id, name),
        )


def remove_from_watchlist(db, provider, entity_type, stashdb_id):
    with db.connect() as conn:
        conn.execute(
            "DELETE FROM watchlist WHERE provider = ? AND entity_type = ? AND stashdb_id = ?",
            (provider, entity_type, stashdb_id),
        )
        conn.execute(
            "DELETE FROM watermark WHERE provider = ? AND entity_type = ? AND stashdb_id = ?",
            (provider, entity_type, stashdb_id),
        )


def set_auto_handoff(db, provider, entity_type, stashdb_id, enabled, from_date=None, until_date=None):
    """Opt-in per-entity auto hand-off to Whisparr (2026-09-13, see
    PROGRESS.md). `from_date`/`until_date` (ISO "YYYY-MM-DD" or None)
    restrict it to scenes whose own release date falls in that window --
    see watcher.py's `_within_auto_handoff_window()`, which reads these
    back at scan time."""
    with db.connect() as conn:
        conn.execute(
            """UPDATE watchlist SET auto_handoff = ?, auto_handoff_from = ?, auto_handoff_until = ?
               WHERE provider = ? AND entity_type = ? AND stashdb_id = ?""",
            (int(bool(enabled)), from_date or None, until_date or None, provider, entity_type, stashdb_id),
        )


def get_feed_watermark(db, provider):
    """Newest `created` already read from this provider's global feed
    (watcher.run_feed_scan(), 2026-09-25) -- kept in the settings table
    under `feed_watermark:<provider>`, no schema change needed."""
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM setting WHERE key = ?", (f"feed_watermark:{provider}",)).fetchone()
        return row["value"] if row else None


def set_feed_watermark(db, provider, last_created):
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO setting (key, value) VALUES (?, ?)
               ON CONFLICT (key) DO UPDATE SET value = excluded.value""",
            (f"feed_watermark:{provider}", last_created),
        )


def get_watermark(db, provider, entity_type, stashdb_id):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT last_created FROM watermark WHERE provider = ? AND entity_type = ? AND stashdb_id = ?",
            (provider, entity_type, stashdb_id),
        ).fetchone()
        return row["last_created"] if row else None


def set_watermark(db, provider, entity_type, stashdb_id, last_created):
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO watermark (provider, entity_type, stashdb_id, last_created, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))
               ON CONFLICT (provider, entity_type, stashdb_id)
               DO UPDATE SET last_created = excluded.last_created, updated_at = excluded.updated_at""",
            (provider, entity_type, stashdb_id, last_created),
        )


# --- Per-entity date window (2026-09-26) ---
# Stored in the existing auto_handoff_from/auto_handoff_until columns
# (named after their original, narrower purpose): since 2026-09-26 the
# window limits EVERYTHING for that entity -- which scenes the scan
# stores, which years/scenes "Missing from Catalog" loads, and auto
# "-> Wishlist". Kept identical on every provider row of the same real
# entity (same local Stash id). An empty From falls back to the global
# `default_window_from` setting.

def set_window_for_entity(db, entity_type, local_id, from_date, until_date):
    with db.connect() as conn:
        cur = conn.execute(
            "UPDATE watchlist SET auto_handoff_from = ?, auto_handoff_until = ? WHERE entity_type = ? AND local_id = ?",
            (from_date or None, until_date or None, entity_type, int(local_id)),
        )
        return cur.rowcount


def effective_window(db, entry):
    """(from, until, from_is_default) for one watchlist row."""
    from routers.settings import get_setting_value
    own_from = entry.get("auto_handoff_from")
    default_from = (get_setting_value(db, "default_window_from", "") or "").strip() or None
    return (own_from or default_from, entry.get("auto_handoff_until"), not own_from and bool(default_from))


def in_window(scene_date, window_from, window_until):
    """A scene WITHOUT a release date is kept (can't tell, and dropping
    it silently would hide it for good); otherwise inclusive bounds."""
    if not scene_date:
        return True
    day = scene_date[:10]
    if window_from and day < window_from:
        return False
    if window_until and day > window_until:
        return False
    return True


def get_entry(db, provider, entity_type, stashdb_id):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM watchlist WHERE provider = ? AND entity_type = ? AND stashdb_id = ?",
            (provider, entity_type, stashdb_id),
        ).fetchone()
        return dict(row) if row else None
