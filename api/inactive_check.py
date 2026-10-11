"""The "probably inactive" review list (2026-09-26, watchlist-by-tag stage 2).

A periodic check collects, per watchlist entity, when its newest scene
was RELEASED (scene date, not the created-at watermark the scan uses)
plus whatever the provider says about it (career end year, death date,
deleted). The review list then proposes entities for removal from the
watchlist -- nothing is removed automatically, the user decides per
entry ("Keep" or "Remove from watchlist").

Criteria (decided 2026-09-26 after checking the data live):
- Proposed when the newest known scene is older than
  `inactive_after_months` (setting, default 18), or there is no scene
  at all, or StashDB reports the entity as deleted/merged (its StashDB
  row then can't find anything new any more).
- Career end year / death date are shown as extra information only,
  never a trigger on their own: ThePornDB reported career_end_year 2012
  for a performer with scenes from August 2026, and posthumous releases
  still count as new scenes.

Cost: StashDB is asked for the whole watchlist in batches (GraphQL
aliases, ~50 requests for ~1250 entities). ThePornDB is only asked as a
second opinion for the entities StashDB suggests are inactive (it often
has newer scenes than StashDB), or for entities that have no StashDB
row at all.
"""
import json
import logging
from datetime import date, datetime, timezone

import watchlist_repo

logger = logging.getLogger(__name__)

REASON_TEXT = {
    "no_recent_scene": "no new scene for a long time",
    "no_scenes": "no scenes at all",
    "deleted_stashdb": "deleted/merged on StashDB",
    "deleted_theporndb": "deleted on ThePornDB",
    "career_ended": "career ended",
    "died": "died",
}
# Reasons that make an entity show up at all; the others only add detail.
TRIGGER_REASONS = {"no_recent_scene", "no_scenes", "deleted_stashdb"}


def _cutoff(months):
    today = date.today()
    total = today.year * 12 + (today.month - 1) - int(months)
    year, month = divmod(total, 12)
    return date(year, month + 1, min(today.day, 28)).isoformat()


def _upsert_activity(db, provider, entity_type, stashdb_id, a):
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO watchlist_activity (provider, entity_type, stashdb_id, newest_scene_date, scene_count,
                                               career_end_year, death_date, deleted, checked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT (provider, entity_type, stashdb_id) DO UPDATE SET
                 newest_scene_date = excluded.newest_scene_date, scene_count = excluded.scene_count,
                 career_end_year = excluded.career_end_year, death_date = excluded.death_date,
                 deleted = excluded.deleted, checked_at = excluded.checked_at""",
            (provider, entity_type, stashdb_id, a.get("newest_scene_date"), a.get("scene_count"),
             a.get("career_end_year"), a.get("death_date"), int(bool(a.get("deleted")))),
        )


def _activity_by_key(db):
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM watchlist_activity").fetchall()
    return {(r["provider"], r["entity_type"], r["stashdb_id"]): dict(r) for r in rows}


def _group_entities(db):
    """Watchlist rows merged per real entity (same local Stash id)."""
    entities = {}
    for row in watchlist_repo.list_watchlist(db):
        key = (row["entity_type"], row["local_id"] if row["local_id"] is not None else f"name:{row['name']}")
        entity = entities.setdefault(key, {"entity_type": row["entity_type"], "local_id": row["local_id"],
                                           "name": row["name"], "rows": []})
        entity["rows"].append(row)
    return entities


def _is_stale(newest, cutoff):
    return newest is None or newest < cutoff


def run_check(db, stashdb_client, theporndb_client, months, progress=None):
    """Refreshes watchlist_activity for the whole watchlist. Returns a
    small summary; the review list itself is computed on read
    (list_review()), so a changed threshold applies immediately."""
    cutoff = _cutoff(months)
    entities = _group_entities(db)
    errors = []

    stashdb_rows = {"studio": [], "performer": []}
    for e in entities.values():
        for row in e["rows"]:
            if row["provider"] == "stashdb":
                stashdb_rows[row["entity_type"]].append(row["stashdb_id"])
    for entity_type, ids in stashdb_rows.items():
        if progress:
            progress(f"StashDB {entity_type}s")
        try:
            for sid, a in stashdb_client.fetch_activity(entity_type, ids).items():
                _upsert_activity(db, "stashdb", entity_type, sid, a)
        except Exception as e:
            logger.exception("inactive check: StashDB %s batch failed", entity_type)
            errors.append(f"StashDB {entity_type}s: {e}")

    # Second opinion from ThePornDB only where needed.
    activity = _activity_by_key(db)
    second = []
    for e in entities.values():
        stash_rows = [r for r in e["rows"] if r["provider"] == "stashdb"]
        tpdb_rows = [r for r in e["rows"] if r["provider"] == "theporndb" and r.get("native_id")]
        if not tpdb_rows:
            continue
        if stash_rows:
            a = activity.get(("stashdb", e["entity_type"], stash_rows[0]["stashdb_id"]))
            if a and not a["deleted"] and not _is_stale(a["newest_scene_date"], cutoff):
                continue
        second.extend(tpdb_rows)
    for n, row in enumerate(second):
        if progress:
            progress(f"ThePornDB {n + 1}/{len(second)}")
        if theporndb_client is None:
            break
        try:
            a = theporndb_client.fetch_activity(row["entity_type"], row["stashdb_id"], row["native_id"])
            _upsert_activity(db, "theporndb", row["entity_type"], row["stashdb_id"], a)
        except Exception as e:
            logger.warning("inactive check: ThePornDB %s %r failed: %s", row["entity_type"], row["name"], e)
            errors.append(f"ThePornDB {row['name']}: {e}")

    summary = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entities": len(entities), "theporndb_checked": len(second),
        "proposed": len(list_review(db, months)), "errors": errors[:20],
    }
    with db.connect() as conn:
        conn.execute("""INSERT INTO setting (key, value) VALUES ('inactive_check_last_run', ?)
                        ON CONFLICT (key) DO UPDATE SET value = excluded.value""", (json.dumps(summary),))
    logger.info("inactive check: %s", {k: v for k, v in summary.items() if k != "errors"})
    return summary


def last_run(db):
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM setting WHERE key = 'inactive_check_last_run'").fetchone()
    try:
        return json.loads(row["value"]) if row else None
    except ValueError:
        return None


def _kept(db):
    with db.connect() as conn:
        rows = conn.execute("SELECT entity_type, local_id, kept_reasons FROM inactive_review").fetchall()
    return {(r["entity_type"], r["local_id"]): set(json.loads(r["kept_reasons"])) for r in rows}


def list_review(db, months):
    """Entities currently proposed as probably inactive, oldest newest-
    scene first. Each: entity_type, local_id, name, newest_scene_date,
    providers, reasons [{key, text}]. Entities never checked yet (no
    activity row) are left out rather than proposed."""
    cutoff = _cutoff(months)
    activity = _activity_by_key(db)
    kept = _kept(db)
    out = []
    for e in _group_entities(db).values():
        acts = {r["provider"]: activity.get((r["provider"], e["entity_type"], r["stashdb_id"])) for r in e["rows"]}
        acts = {p: a for p, a in acts.items() if a}
        if not acts:
            continue
        dates = [a["newest_scene_date"] for a in acts.values() if a["newest_scene_date"]]
        newest = max(dates) if dates else None
        reasons = []
        if newest is None:
            if all((a["scene_count"] or 0) == 0 for a in acts.values()):
                reasons.append({"key": "no_scenes", "text": REASON_TEXT["no_scenes"]})
        elif newest < cutoff:
            reasons.append({"key": "no_recent_scene", "text": f"newest scene {newest}"})
        for p, a in acts.items():
            if a["deleted"] and p in ("stashdb", "theporndb"):
                reasons.append({"key": f"deleted_{p}", "text": REASON_TEXT[f"deleted_{p}"]})
        career = [a["career_end_year"] for a in acts.values() if a["career_end_year"]]
        if career:
            reasons.append({"key": "career_ended", "text": f"career ended {max(career)}"})
        deaths = [a["death_date"] for a in acts.values() if a["death_date"]]
        if deaths:
            reasons.append({"key": "died", "text": f"died {deaths[0]}"})
        keys = {r["key"] for r in reasons}
        if not keys & TRIGGER_REASONS:
            continue
        if e["local_id"] is not None and keys <= kept.get((e["entity_type"], e["local_id"]), set()):
            continue  # kept before, nothing new since
        out.append({
            "entity_type": e["entity_type"], "local_id": e["local_id"], "name": e["name"],
            "newest_scene_date": newest, "providers": sorted(acts), "reasons": reasons,
        })
    out.sort(key=lambda x: (x["newest_scene_date"] or "", x["name"].lower()))
    return out


def keep(db, entity_type, local_id, reason_keys):
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO inactive_review (entity_type, local_id, kept_reasons, kept_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT (entity_type, local_id) DO UPDATE SET
                 kept_reasons = excluded.kept_reasons, kept_at = excluded.kept_at""",
            (entity_type, int(local_id), json.dumps(sorted(set(reason_keys)))),
        )
