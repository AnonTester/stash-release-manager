"""Pictures of scenes, performers and studios for the plugin's lists and Search page (v0.256.0, see scene_covers.py)."""
import os

from fastapi import APIRouter

import metadata_provider_repo
import scene_covers

router = APIRouter(prefix="/covers", tags=["covers"])

_covers = None
_get_stash_client = None
_get_provider_client = None
_db = None


def init_covers_router(db, get_stash_client, get_provider_client):
    global _db, _get_stash_client, _get_provider_client, _covers
    _db, _get_stash_client, _get_provider_client, _covers = db, get_stash_client, get_provider_client, None


def _endpoint_of(provider):
    """The stash-box endpoint a local Stash entry is tagged with for this provider (None = unknown)."""
    if provider in ("stashdb", "theporndb"):
        return _get_stash_client().endpoint_for_provider(provider)
    row = metadata_provider_repo.get_one(_db, provider)
    return row["url"] if row else None


def get_covers():
    global _covers
    if _covers is None:
        _covers = scene_covers.SceneCovers(_db, os.path.dirname(str(_db.path)), _get_stash_client, _get_provider_client, endpoint_of=_endpoint_of)
    return _covers


@router.post("")
def covers_for(body: dict):
    """The picture of each item of one page: {"kind:provider:id": {"stash": local id} | {"data": data URL} | {"pending": true} | null}.
    In Stash -> Stash's own picture (the cached copy is deleted); otherwise the stash-box's, fetched once, scaled and cached."""
    items = [(x.get("kind") or "scene", x.get("provider") or "stashdb", str(x.get("id")))
             for x in (body.get("items") or [])[:200] if x.get("id")]
    return {"covers": get_covers().covers_for(items)}
