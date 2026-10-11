"""History/Events log (2026-09-28, v0.79.0) -- the *arrs' History +
System > Events in one table (db.py's `history`).

- kind 'history': something happened to ONE scene (added to the wishlist,
  grabbed, imported, import failed, blocklisted, dismissed, ...) --
  carries provider/scene_id/scene_title so the UI can link and filter.
- kind 'event': the system (job results, connection errors, settings
  changes, sidecar start, error pop-ups from the browser).

Agreed with the user (see project memory): no entries for job runs
without a result, "already running" skips or copy internals. Pruned by
date, `history_retention_days` (default 14).

add() never raises -- logging must not break the action it describes --
and pushes a live 'history' event (events.py) so an open History page
updates itself.
"""
import json
import logging

import events

logger = logging.getLogger(__name__)

_db = None

LEVELS = ("info", "warning", "error")
KINDS = ("history", "event")


def init(db):
    global _db
    _db = db


def add(level, kind, category, message, provider=None, scene_id=None, scene_title=None, details=None,
        dedupe_minutes=None):
    """Writes one entry. `dedupe_minutes`: skip it if the same category +
    message (+ scene) was already logged within that many minutes -- for
    conditions that are re-detected every cycle (a connection error, a
    missing source file)."""
    if _db is None:
        return
    try:
        if scene_id and not scene_title:
            scene_title = _scene_title(provider, scene_id)
        details_json = json.dumps(details, default=str) if details is not None else None
        with _db.connect() as conn:
            if dedupe_minutes:
                dup = conn.execute(
                    """SELECT 1 FROM history WHERE category = ? AND message = ?
                       AND COALESCE(scene_id, '') = COALESCE(?, '')
                       AND at >= strftime('%Y-%m-%d %H:%M:%S', 'now', ?) LIMIT 1""",
                    (category, message, scene_id, f"-{int(dedupe_minutes)} minutes")).fetchone()
                if dup:
                    return
            conn.execute(
                """INSERT INTO history (level, kind, category, message, details, provider, scene_id, scene_title)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (level if level in LEVELS else "info", kind if kind in KINDS else "event", category,
                 message, details_json, provider, scene_id, scene_title))
        events.publish("history", category=category, level=level)
    except Exception:
        logger.exception("history: couldn't write %s/%s %r", kind, category, message)


def scene(level, category, message, provider, scene_id, scene_title=None, details=None, dedupe_minutes=None):
    """Shortcut for a per-scene ('history') entry."""
    add(level, "history", category, message, provider=provider, scene_id=scene_id, scene_title=scene_title,
        details=details, dedupe_minutes=dedupe_minutes)


def event(level, category, message, details=None, dedupe_minutes=None):
    """Shortcut for a system ('event') entry."""
    add(level, "event", category, message, details=details, dedupe_minutes=dedupe_minutes)


def _scene_title(provider, scene_id):
    with _db.connect() as conn:
        row = conn.execute("SELECT title FROM release WHERE provider = ? AND scene_stashdb_id = ? LIMIT 1",
                           (provider or "stashdb", scene_id)).fetchone()
    return row["title"] if row else None


def query(q="", levels=None, kinds=None, categories=None, provider=None, scene_id=None,
          date_from=None, date_until=None, page=1, per_page=50):
    """Newest first, paged: {total, rows, categories}."""
    where, params = [], []
    if q:
        where.append("(message LIKE ? OR scene_title LIKE ? OR details LIKE ?)")
        params += [f"%{q}%"] * 3
    for column, values in (("level", levels), ("kind", kinds), ("category", categories)):
        if values:
            where.append(f"{column} IN ({','.join('?' * len(values))})")
            params += list(values)
    if scene_id:
        where.append("scene_id = ?")
        params.append(scene_id)
        if provider:
            where.append("provider = ?")
            params.append(provider)
    if date_from:
        where.append("at >= ?")
        params.append(date_from)
    if date_until:
        where.append("at < date(?, '+1 day')")
        params.append(date_until)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    per_page = max(1, min(int(per_page or 50), 500))
    page = max(1, int(page or 1))
    with _db.connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM history {where_sql}", params).fetchone()["n"]
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM history {where_sql} ORDER BY at DESC, id DESC LIMIT ? OFFSET ?",
            params + [per_page, (page - 1) * per_page])]
        categories = [r["category"] for r in conn.execute("SELECT DISTINCT category FROM history ORDER BY category")]
    for r in rows:
        if r["details"]:
            try:
                r["details"] = json.loads(r["details"])
            except ValueError:
                pass
    return {"total": total, "rows": rows, "categories": categories}


def prune(retention_days):
    """Deletes entries older than `retention_days` (0 = keep everything).
    Returns how many were removed."""
    if _db is None or not retention_days or retention_days <= 0:
        return 0
    with _db.connect() as conn:
        cur = conn.execute("DELETE FROM history WHERE at < strftime('%Y-%m-%d %H:%M:%S', 'now', ?)",
                           (f"-{int(retention_days)} days",))
    return cur.rowcount
