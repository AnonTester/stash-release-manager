"""Dismiss streak per studio (v0.216.0): how many releases of a (sub-)studio the user dismissed one after the other without putting one of that
studio on the wishlist. At 10 the Dismiss card offers to hide the whole studio instead (like "Hide studio"); a "no" -- or just ignoring the card --
means the next offer comes after 10 more dismisses. One row per studio, so the table stays small and needs no time window.

Counted: a dismiss of a release that came in through a STUDIO and was still 'new' / 'backlog'. Not counted: the bulk "Dismiss N shown", dismissing
something already on the wishlist (Stuck), performer hits (a studio can only be hidden for what the studio brought in), upgrade/film rows.
Reset to 0: the studio gets a release on the wishlist or imported (release_repo._update_status). The Undo of a dismiss takes one back.
"""
import re

THRESHOLD = 10
COUNTED_STATUSES = ("new", "backlog")


def ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS studio_dismiss_streak (
        studio_id      TEXT PRIMARY KEY,
        provider       TEXT NOT NULL DEFAULT 'stashdb',
        name           TEXT NOT NULL DEFAULT '',
        count          INTEGER NOT NULL DEFAULT 0,
        asked_at_count INTEGER NOT NULL DEFAULT 0,
        updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
    )""")


def _studio_of(conn, provider, scene_id):
    r = conn.execute("SELECT studio_id, studio_name FROM release WHERE provider = ? AND scene_stashdb_id = ? AND studio_id IS NOT NULL "
                     "LIMIT 1", (provider, scene_id)).fetchone()
    return (r["studio_id"], r["studio_name"] or "") if r else (None, "")


def _counts(previous_rows):
    """True when this dismiss counts: a row that came in through a studio and was still an open suggestion."""
    return any(r.get("entity_type") == "studio" and r.get("status") in COUNTED_STATUSES
               and not str(r.get("entity_stashdb_id") or "").startswith(("upgrade:", "film:")) for r in previous_rows or [])


def on_dismiss(db, provider, scene_id, previous_rows):
    """Called after a single dismiss. Returns {"studio_id", "name", "count"} when the user should be asked, else None."""
    if not _counts(previous_rows):
        return None
    with db.connect() as conn:
        studio_id, name = _studio_of(conn, provider, scene_id)
        if not studio_id or conn.execute("SELECT 1 FROM muted_studio WHERE studio_id = ?", (studio_id,)).fetchone():
            return None
        conn.execute("""INSERT INTO studio_dismiss_streak (studio_id, provider, name, count) VALUES (?, ?, ?, 1)
                        ON CONFLICT (studio_id) DO UPDATE SET count = count + 1, name = excluded.name, updated_at = datetime('now')""",
                     (studio_id, provider, name))
        row = conn.execute("SELECT count, asked_at_count FROM studio_dismiss_streak WHERE studio_id = ?", (studio_id,)).fetchone()
        if row["count"] >= THRESHOLD and row["count"] - row["asked_at_count"] >= THRESHOLD:
            conn.execute("UPDATE studio_dismiss_streak SET asked_at_count = count WHERE studio_id = ?", (studio_id,))
            return {"studio_id": studio_id, "name": name, "count": row["count"]}
    return None


def on_restore(db, rows):
    """The Undo of a dismiss: one less for the studio of each restored scene (only rows that would have counted)."""
    scenes = {(r["provider"], r["scene_stashdb_id"]) for r in rows if r.get("entity_type") == "studio" and r.get("status") in COUNTED_STATUSES
              and not str(r.get("entity_stashdb_id") or "").startswith(("upgrade:", "film:"))}
    with db.connect() as conn:
        for provider, scene_id in scenes:
            studio_id, _ = _studio_of(conn, provider, scene_id)
            if studio_id:
                conn.execute("UPDATE studio_dismiss_streak SET count = MAX(count - 1, 0), asked_at_count = MIN(asked_at_count, MAX(count - 1, 0)) "
                             "WHERE studio_id = ?", (studio_id,))


def forget(conn, studio_id):
    conn.execute("DELETE FROM studio_dismiss_streak WHERE studio_id = ?", (studio_id,))


def seed_from_history(conn):
    """One-time start values when the table is created: replays the History (dismiss / wishlist / grab / import events per scene, oldest first),
    so the streaks of the last days are not lost. Approximation: a dismiss of a scene that had a wishlist event before is not counted (that was a
    Stuck dismiss); hidden studios are skipped. Only as far back as the History reaches."""
    try:
        events = conn.execute("""SELECT category, message, provider, scene_id FROM history
                                 WHERE scene_id IS NOT NULL AND category IN ('dismiss', 'wishlist', 'grab', 'import')
                                 ORDER BY at, id""").fetchall()
    except Exception:
        return
    muted = {r["studio_id"] for r in conn.execute("SELECT studio_id FROM muted_studio")}
    studio_cache, on_list, counts = {}, set(), {}
    for e in events:
        key = (e["provider"], e["scene_id"])
        if key not in studio_cache:
            studio_cache[key] = _studio_of(conn, *key)
        studio_id, name = studio_cache[key]
        if not studio_id or studio_id in muted:
            continue
        if e["category"] == "dismiss":
            if e["message"] != "Dismissed" or key in on_list:
                continue
            via_studio = conn.execute("SELECT 1 FROM release WHERE provider = ? AND scene_stashdb_id = ? AND entity_type = 'studio' "
                                      "AND entity_stashdb_id NOT LIKE 'upgrade:%' AND entity_stashdb_id NOT LIKE 'film:%' LIMIT 1", key).fetchone()
            if via_studio:
                counts[studio_id] = (counts.get(studio_id, (0, name))[0] + 1, name)
        else:
            on_list.add(key)
            if studio_id in counts:
                counts[studio_id] = (0, name)
    for studio_id, (n, name) in counts.items():
        if n:
            conn.execute("INSERT OR REPLACE INTO studio_dismiss_streak (studio_id, name, count, asked_at_count) VALUES (?, ?, ?, 0)", (studio_id, name, n))
