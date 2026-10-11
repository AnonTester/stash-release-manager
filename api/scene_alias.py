"""Alias provider ids (2026-10-02).

A local Stash scene holds ONE id per stash-box, but a producer may sell the same video under two titles (two
StashDB scenes). After the two local scenes are merged only one provider scene is "in the library" -- the other
one would show up as MISSING in the plugin's "Missing from Catalog" list, could be put on the wishlist and be
found/downloaded again by the RSS search, and would keep the completeness ratio from reaching 100 %.

`scene_alias` records "provider scene X is owned as local scene Y". The in-library checks consult it
(stash_client.library_heights_by_stash_ids / find_scene_by_stashdb_id via `StashClient.alias_lookup`, and the
completeness numerator in routers/backlog.py via count_for_entity()).
"""
import json
import logging

logger = logging.getLogger(__name__)


KIND_ALIAS = "alias"                # one video, two TITLES at the studio
KIND_DUPLICATE = "duplicate_entry"  # StashDB lists the same scene twice under the same title (watched for a merge, v0.125.0)


def add(db, alias_scene_id, local_scene_id, provider="stashdb", title=None, scene_date=None,
        studio_stashdb_id=None, performer_stashdb_ids=None, note=None, kind=KIND_ALIAS, partner_scene_id=None):
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO scene_alias (provider, alias_scene_id, local_scene_id, title, scene_date,
                                        studio_stashdb_id, performer_stashdb_ids, note, kind, partner_scene_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (provider, alias_scene_id) DO UPDATE SET
                   local_scene_id = excluded.local_scene_id, note = COALESCE(excluded.note, note),
                   kind = excluded.kind, partner_scene_id = COALESCE(excluded.partner_scene_id, partner_scene_id),
                   title = COALESCE(excluded.title, title), scene_date = COALESCE(excluded.scene_date, scene_date),
                   studio_stashdb_id = COALESCE(excluded.studio_stashdb_id, studio_stashdb_id),
                   performer_stashdb_ids = COALESCE(excluded.performer_stashdb_ids, performer_stashdb_ids)""",
            (provider, alias_scene_id, str(local_scene_id), title, scene_date, studio_stashdb_id,
             json.dumps(performer_stashdb_ids) if performer_stashdb_ids is not None else None, note, kind, partner_scene_id))


def add_from_provider(db, provider_client, alias_scene_id, local_scene_id, provider="stashdb", note=None, kind=KIND_ALIAS, partner_scene_id=None):
    """Looks the provider scene up once (title/date/studio/performers, needed to count the alias towards the
    right Studio/Performer in the completeness ratio) and records the alias. A failed lookup still records it."""
    title = date = studio = performers = None
    try:
        d = provider_client.get_scene_detail(alias_scene_id) if provider_client else None
        if d:
            title, date = d.get("title"), d.get("date") or d.get("release_date")
            studio = (d.get("studio") or {}).get("id")
            performers = [(p.get("performer") or p).get("id") for p in (d.get("performers") or [])]
    except Exception:
        logger.exception("alias: provider lookup failed for %s", alias_scene_id)
    add(db, alias_scene_id, local_scene_id, provider, title, date, studio, performers, note, kind, partner_scene_id)


def remove(db, row_id):
    with db.connect() as conn:
        conn.execute("DELETE FROM scene_alias WHERE id = ?", (row_id,))


def list_all(db):
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM scene_alias ORDER BY id DESC").fetchall()]
    for r in rows:
        r["performer_stashdb_ids"] = json.loads(r["performer_stashdb_ids"]) if r.get("performer_stashdb_ids") else []
    return rows


def local_map(db, ids, provider="stashdb"):
    """{alias_scene_id: local_scene_id} for the given provider scene ids that are aliases."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    out = {}
    with db.connect() as conn:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ",".join("?" * len(chunk))
            for r in conn.execute(f"SELECT alias_scene_id, local_scene_id FROM scene_alias "
                                  f"WHERE provider = ? AND alias_scene_id IN ({q})", [provider, *chunk]):
                out[r["alias_scene_id"]] = r["local_scene_id"]
    return out


def for_entity(db, entity_type, entity_stashdb_id, provider="stashdb"):
    """Alias rows that belong to this StashDB Studio/Performer (from the provider data stored with the alias)."""
    rows = [r for r in list_all(db) if r["provider"] == provider]
    if entity_type == "studio":
        return [r for r in rows if r["studio_stashdb_id"] == entity_stashdb_id]
    return [r for r in rows if entity_stashdb_id in (r["performer_stashdb_ids"] or [])]


def count_for_entity(db, stash_client, entity_type, entity_stashdb_id, min_height):
    """How many alias provider scenes of this entity are owned (their local scene has a file >= min_height).
    Added to the completeness numerator: every alias is one more StashDB scene of the entity's catalogue."""
    rows = for_entity(db, entity_type, entity_stashdb_id)
    if not rows:
        return 0
    heights = stash_client.heights_by_scene_ids({r["local_scene_id"] for r in rows})
    return sum(1 for r in rows if heights.get(str(r["local_scene_id"]), 0) >= (min_height or 0))


# ---------- StashDB duplicate entries: watched for a merge (v0.125.0) ----------

def check_duplicates(db, stashdb_client):
    """ONE bundled StashDB request (<= 25 scenes per request) for every OPEN duplicate entry: both its StashDB ids (the listed duplicate and
    the id the local scene carries). When StashDB merged/deleted one of them the row becomes 'merged' (StashDB answered with another id:
    `merged_into`) or 'gone' (unknown/deleted) and is returned, so the caller can log it. Nothing else is changed."""
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM scene_alias WHERE kind = ? AND status = 'open' AND provider = 'stashdb'", (KIND_DUPLICATE,)).fetchall()]
    if not rows:
        return {"checked": 0, "changed": []}
    ids = sorted({i for r in rows for i in (r["alias_scene_id"], r["partner_scene_id"]) if i})
    state = stashdb_client.get_scenes_state_batch(ids)
    changed = []
    with db.connect() as conn:
        for r in rows:
            new_status, merged_into = "open", None
            a, b = r["alias_scene_id"], r["partner_scene_id"]
            sa, sb = state.get(a), state.get(b) if b else None
            for mine, other_id, other in ((sa, b, sb), (sb, a, sa)):
                if not mine or mine["state"] == "ok":
                    continue
                if mine["state"] == "redirect":  # StashDB answered with another scene: that one survives
                    new_status, merged_into = "merged", mine["id"]
                elif other and other["state"] == "ok":  # StashDB keeps merged-away scenes but flags them deleted: the other one survives
                    new_status, merged_into = "merged", other_id
                else:
                    new_status = "gone"
                break
            conn.execute("UPDATE scene_alias SET checked_at = datetime('now'), status = ?, merged_into = ? WHERE id = ?",
                         (new_status, merged_into, r["id"]))
            if new_status != "open":
                changed.append({**r, "status": new_status, "merged_into": merged_into})
    return {"checked": len(rows), "changed": changed}
