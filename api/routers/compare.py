"""Compare scenes endpoints (2026-09-30, see scene_compare.py)."""
from fastapi import APIRouter, HTTPException

import scene_compare
import upgrades

router = APIRouter(prefix="/maintenance/compare", tags=["compare"])

_db = None
_compare = None


def init_compare_router(db, compare_factory):
    global _db, _compare
    _db = db
    _compare = compare_factory


@router.get("/counts")
def counts():
    """What waits for a decision, for the badges of the Maintenance side menu (v0.153.0): Review scenes still open, Compare pairs
    still open, Upgrade candidates still wanted or to be reviewed. Local only (this DB, one Stash request for the Review ids)."""
    out = {"pairs_open": len(scene_compare.list_pairs(_db, "pending")),
           "upgrades_open": len(upgrades.list_candidates(_db, ("wanted", "review"))), "review_open": None}
    try:
        out["review_open"] = _compare().review_open_count()
    except Exception:
        pass  # Stash not reachable: no badge for Review
    return out


@router.get("/pairs")
def pairs(status: str = ""):
    """Every pair with a light summary of both scenes (title/resolution/length from Stash, no provider requests)."""
    out = []
    comp = _compare()
    stash = comp.get_stash()
    for p in scene_compare.list_pairs(_db, status or None):
        for side in ("a", "b"):
            try:
                sc = stash._graphql("query($id: ID!) { findScene(id: $id) { id title date studio { name } "
                                    "files { width height duration size } } }", {"id": p[f"scene_{side}"]})["findScene"]
            except Exception as e:
                sc = None
                p[f"{side}_error"] = str(e)
            f = (sc or {}).get("files") or [{}]
            p[side] = None if not sc else {"id": sc["id"], "title": sc["title"], "date": sc["date"],
                                          "studio": (sc["studio"] or {}).get("name"), "height": f[0].get("height"),
                                          "minutes": round((f[0].get("duration") or 0) / 60, 1),
                                          "mb": round((f[0].get("size") or 0) / 1e6)}
        out.append(p)
    return {"pairs": out}


@router.post("/pairs")
def add_pairs(body: dict):
    items = body.get("pairs") or ([{"a": body.get("a"), "b": body.get("b"), "label": body.get("label")}] if body.get("a") else [])
    if not items:
        raise HTTPException(400, "a and b (scene ids) are required")
    return {"added": scene_compare.add_pairs(_db, items)}


@router.get("/pair/{pair_id}")
def pair(pair_id: int):
    try:
        p = _compare().pair(pair_id)
    except Exception as e:
        raise HTTPException(502, f"could not read the pair from Stash: {e}")
    if not p:
        raise HTTPException(404, "pair not found")
    return p


@router.post("/pair/{pair_id}/status")
def pair_status(pair_id: int, body: dict):
    try:
        scene_compare.set_status(_db, pair_id, body.get("status"), body.get("note"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.delete("/pair/{pair_id}")
def pair_delete(pair_id: int):
    scene_compare.delete_pair(_db, pair_id)
    return {"ok": True}


@router.post("/pair/{pair_id}/remove-id")
def pair_remove_id(pair_id: int, body: dict):
    if not body.get("scene_id") or not body.get("stash_id"):
        raise HTTPException(400, "scene_id and stash_id are required")
    return _compare().remove_id(pair_id, body["scene_id"], body["stash_id"])


@router.post("/pair/{pair_id}/identify")
def pair_identify(pair_id: int, body: dict):
    return _compare().identify(pair_id, body.get("scene_id"))


@router.post("/pair/{pair_id}/search")
def pair_search(pair_id: int, body: dict):
    return _compare().search(pair_id, body.get("scene_id"), body.get("query"))


@router.post("/pair/{pair_id}/adopt")
def pair_adopt(pair_id: int, body: dict):
    if not body.get("scene_id") or not body.get("provider") or not body.get("provider_id"):
        raise HTTPException(400, "scene_id, provider and provider_id are required")
    return _compare().adopt(pair_id, body["scene_id"], body["provider"], body["provider_id"])


@router.post("/pair/{pair_id}/reset-metadata")
def pair_reset(pair_id: int, body: dict):
    return _compare().reset_metadata(pair_id, body.get("scene_id"))


@router.get("/review")
def review_list():
    """Scenes tagged 'Review: Identify Scene' (single cases, no pair)."""
    return _compare().review_scenes()


@router.get("/unidentified")
def unidentified():
    """Scenes without any provider id outside the film folders (v0.170.0), one Stash request."""
    return _compare().unidentified_list()


@router.post("/unidentified/check")
def unidentified_check(body: dict):
    """Dry run: StashDB fingerprint lookup for up to 200 scenes, nothing is written."""
    return {"results": _compare().unidentified_check([str(x) for x in (body.get("scene_ids") or [])])}


@router.post("/unidentified/apply")
def unidentified_apply(body: dict):
    """Assigns the StashDB scene to the scenes that were a clear match in the last check (up to 50 per call)."""
    return {"results": _compare().unidentified_apply([str(x) for x in (body.get("scene_ids") or [])])}


@router.post("/review/{scene_id}/add")
def review_add(scene_id: str):
    """Sends a scene to Review > Identify Scene: ADDS the review tag, nothing else on the scene changes."""
    return _compare().review_add(scene_id)


@router.get("/review/{scene_id}")
def review_scene(scene_id: str):
    return _compare().scene(scene_id)


@router.post("/review/{scene_id}/status")
def review_status(scene_id: str, body: dict):
    return _compare().review_status(scene_id, body.get("status") == "later", body.get("note"))


@router.post("/review/{scene_id}/move-unmatched")
def review_move_unmatched(scene_id: str):
    return _compare().move_unmatched(scene_id)


@router.get("/review/{scene_id}/movie")
def review_movie(scene_id: str):
    return _compare().movie_info(scene_id)


@router.post("/review/{scene_id}/movie-search")
def review_movie_search(scene_id: str, body: dict):
    return _compare().movie_search(scene_id, body.get("q"))


@router.post("/review/{scene_id}/movie-create")
def review_movie_create(scene_id: str, body: dict):
    if not body.get("tpdb_id"):
        raise HTTPException(400, "tpdb_id is required")
    return _compare().movie_create(scene_id, body["tpdb_id"])


@router.post("/review/{scene_id}/movie-create-manual")
def review_movie_create_manual(scene_id: str, body: dict):
    return _compare().movie_create_manual(scene_id, body.get("name"))


@router.post("/review/{scene_id}/group-studio")
def review_group_studio(scene_id: str, body: dict):
    return _compare().group_studio(scene_id, body.get("name"), bool(body.get("clear")))


@router.post("/review/{scene_id}/group-add")
def review_group_add(scene_id: str, body: dict):
    if not body.get("group_id"):
        raise HTTPException(400, "group_id is required")
    return _compare().group_add(scene_id, str(body["group_id"]))


@router.post("/review/{scene_id}/done")
def review_done(scene_id: str):
    return _compare().review_done(scene_id)


@router.get("/sense")
def sense():
    """Is the separate Stash-Sense 2 plugin present? (decides whether the Review area is offered)"""
    return _compare().sense_status()


@router.post("/faces")
def faces(body: dict):
    """Face recognition of one frame/crop via Stash-Sense 2."""
    return _compare().faces(body.get("image_base64"), body.get("top_k") or 5)


@router.post("/pair/{pair_id}/add-performer")
def pair_add_performer(pair_id: int, body: dict):
    if not body.get("scene_id") or not body.get("performer_id"):
        raise HTTPException(400, "scene_id and performer_id are required")
    return _compare().add_performer(pair_id, body["scene_id"], body["performer_id"])


@router.get("/scene/{scene_id}/frames")
def scene_frames(scene_id: str):
    """Real frames from the file (the Stash screenshot is usually the provider's cover image)."""
    return _compare().frames(scene_id)


@router.post("/close-resolved")
def close_resolved(body: dict):
    return _compare().close_resolved(dry_run=bool(body.get("dry_run", True)))


@router.get("/scrapers")
def scrapers():
    return _compare().scrapers()


@router.post("/scrape")
def scrape(body: dict):
    return _compare().scrape(body.get("scraper_id") or "AdultEmpire", body.get("query"))


@router.post("/pair/{pair_id}/apply-scrape")
def apply_scrape(pair_id: int, body: dict):
    if not body.get("scene_id"):
        raise HTTPException(400, "scene_id is required")
    return _compare().apply_scrape(pair_id, body["scene_id"], body.get("title"), body.get("date"), body.get("details"), body.get("url"))


@router.get("/aliases")
def aliases():
    return _compare().aliases()


@router.post("/aliases")
def add_alias(body: dict):
    if not body.get("alias_scene_id") or not body.get("local_scene_id"):
        raise HTTPException(400, "alias_scene_id and local_scene_id are required")
    return _compare().add_alias(body["alias_scene_id"], body["local_scene_id"], body.get("note"))


@router.delete("/aliases/{row_id}")
def delete_alias(row_id: int):
    import scene_alias
    scene_alias.remove(_db, row_id)
    return {"ok": True}


@router.get("/aliases/{row_id}/resolve-preview")
def duplicate_resolve_preview(row_id: int):
    return _compare().duplicate_resolve_preview(row_id)


@router.post("/aliases/{row_id}/resolve")
def duplicate_resolve(row_id: int):
    res = _compare().duplicate_resolve(row_id)
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return res


@router.post("/aliases/resolve-undo")
def duplicate_resolve_undo(body: dict):
    return _compare().duplicate_resolve_undo(body.get("applied") or [], body.get("row") or {})
