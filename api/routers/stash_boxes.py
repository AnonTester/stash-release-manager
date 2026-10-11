"""Takes over the Stash API key and the stash-box endpoints of the local Stash (v0.186.0) -- see stashbox_sync.py. Called by the plugin's exec
script, which reads them from Stash itself; the response never contains a key."""
from fastapi import APIRouter
from pydantic import BaseModel

import stashbox_sync

router = APIRouter(prefix="/stash-boxes", tags=["stash-boxes"])

_db = None


def init_stash_boxes_router(db):
    global _db
    _db = db


class StashBox(BaseModel):
    name: str = ""
    endpoint: str = ""
    api_key: str = ""


class SyncBody(BaseModel):
    stash_api_key: str = ""
    stash_url_suggested: str = ""
    boxes: list[StashBox] = []
    add: list[str] = []
    overwrite: bool = False


@router.post("/sync")
def sync(body: SyncBody):
    return stashbox_sync.apply_sync(
        _db, body.stash_api_key, [b.model_dump() for b in body.boxes], add=body.add, overwrite=body.overwrite,
        stash_url_suggested=body.stash_url_suggested)
