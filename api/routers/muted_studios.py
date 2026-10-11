"""Hidden studios (2026-10-03, v0.91.0, see release_repo.mute_studio()): a (sub-)studio the user does not want in the Calendar/List
although its parent studio is watched."""
from fastapi import APIRouter, HTTPException

import time

import release_repo

router = APIRouter(prefix="/muted-studios", tags=["muted-studios"])

_db = None
_get_provider_client = None


def init_muted_studios_router(db, get_provider_client):
    global _db, _get_provider_client
    _db, _get_provider_client = db, get_provider_client


_children_cache = {}  # studio id -> (fetched at, children); sub-studio lists change rarely


@router.get("/children")
def children(studio_id: str):
    """Sub-studios of a StashDB studio for the studio panel's checklist: each with `muted` (hidden by the user) and `releases`
    (new/backlog/hidden releases that came in through a studio). One StashDB request per hour per studio."""
    cached = _children_cache.get(studio_id)
    if cached and time.time() - cached[0] < 3600:
        kids = cached[1]
    else:
        client = _get_provider_client("stashdb")
        if client is None:
            raise HTTPException(503, "StashDB is not configured")
        kids = client.get_child_studios(studio_id)
        _children_cache[studio_id] = (time.time(), kids)
    muted = {m["studio_id"] for m in release_repo.list_muted_studios(_db)}
    by_id, by_name = release_repo.studio_release_counts(_db)
    return {"studios": [{**k, "muted": k["id"] in muted, "releases": by_id.get(k["id"], 0) + by_name.get(k["name"], 0)} for k in kids]}


@router.get("")
def list_muted():
    return {"studios": release_repo.list_muted_studios(_db)}


@router.post("")
def mute(body: dict):
    """Body: {studio_id, name, provider?, parent_id?} or {provider, scene_id} (the studio of a release row: taken from the stored
    row, else looked up at the provider -- one request)."""
    provider = body.get("provider") or "stashdb"
    studio_id, name = (body.get("studio_id") or "").strip(), (body.get("name") or "").strip()
    scene_id = body.get("scene_id")
    if not studio_id and scene_id:
        studio_id, name = release_repo.studio_of_scene(_db, provider, scene_id)
        if not studio_id:
            client = _get_provider_client(provider)
            detail = client.get_scene_detail(scene_id) if client else None
            studio = (detail or {}).get("studio") or {}
            studio_id, name = studio.get("id"), studio.get("name") or name
    if not studio_id:
        raise HTTPException(400, "the scene's studio could not be determined")
    hidden = release_repo.mute_studio(_db, studio_id, name, provider, body.get("parent_id"))
    return {"ok": True, "studio_id": studio_id, "name": name, "hidden": hidden}


@router.post("/unmute")
def unmute(body: dict):
    if not body.get("studio_id"):
        raise HTTPException(400, "studio_id is required")
    return {"ok": True, "restored": release_repo.unmute_studio(_db, body["studio_id"])}
