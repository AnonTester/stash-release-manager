"""Storage for the backlog year-count overview cache (2026-09-13, see
PROGRESS.md's "Cache the backlog year overview" section) -- persists
stashdb_client.count_scenes_by_year()'s result per entity so opening
the Backlog panel doesn't re-run its ~12 StashDB requests every time;
only an explicit refresh (routers/backlog.py's years() with
refresh=True) does.
"""
import json


def get_year_overview(db, provider, entity_type, entity_stashdb_id):
    """Returns {"years": {year: count}, "checked_at": str, "cached": True}
    or None if this entity has never been checked."""
    with db.connect() as conn:
        row = conn.execute(
            """SELECT years_json, checked_at FROM backlog_overview
               WHERE provider = ? AND entity_type = ? AND entity_stashdb_id = ?""",
            (provider, entity_type, entity_stashdb_id),
        ).fetchone()
    if not row:
        return None
    return {"years": json.loads(row["years_json"]), "checked_at": row["checked_at"], "cached": True}


def set_year_overview(db, provider, entity_type, entity_stashdb_id, counts):
    """Upserts the cached overview and returns it in the same shape
    get_year_overview() would (re-reading rather than constructing it
    by hand, so both paths always agree on `checked_at`'s exact
    format)."""
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO backlog_overview (provider, entity_type, entity_stashdb_id, years_json, checked_at)
               VALUES (?, ?, ?, ?, datetime('now'))
               ON CONFLICT (provider, entity_type, entity_stashdb_id) DO UPDATE SET
                   years_json = excluded.years_json, checked_at = excluded.checked_at""",
            (provider, entity_type, entity_stashdb_id, json.dumps({str(k): v for k, v in counts.items()})),
        )
    return get_year_overview(db, provider, entity_type, entity_stashdb_id)


def mark_year_loaded(db, provider, entity_type, entity_stashdb_id, year):
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO backlog_loaded_year (provider, entity_type, entity_stashdb_id, year, loaded_at)
               VALUES (?, ?, ?, ?, datetime('now'))
               ON CONFLICT (provider, entity_type, entity_stashdb_id, year) DO UPDATE SET loaded_at = excluded.loaded_at""",
            (provider, entity_type, entity_stashdb_id, int(year)),
        )


def loaded_years(db, provider, entity_type, entity_stashdb_id):
    """{year: loaded_at or None} -- explicitly recorded loads (2026-09-26)
    plus, for loads from before that, any year that still has scenes
    fetched through this panel (status 'backlog' that aren't scan-found
    late additions, or since sent to the wishlist)."""
    with db.connect() as conn:
        out = {r["year"]: r["loaded_at"] for r in conn.execute(
            """SELECT year, loaded_at FROM backlog_loaded_year
               WHERE provider = ? AND entity_type = ? AND entity_stashdb_id = ?""",
            (provider, entity_type, entity_stashdb_id))}
        for r in conn.execute(
                """SELECT DISTINCT CAST(substr(scene_date, 1, 4) AS INTEGER) AS year FROM release
                   WHERE provider = ? AND entity_type = ? AND entity_stashdb_id = ?
                     AND scene_date IS NOT NULL AND scene_date != ''
                     AND ((status = 'backlog' AND late_addition = 0) OR status IN ('sent_to_whisparr', 'import_failed'))""",
                (provider, entity_type, entity_stashdb_id)):
            out.setdefault(r["year"], None)
    return out
