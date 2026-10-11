"""CRUD + connection check for this sidecar's own direct Newznab
indexers (2026-09-25, Indexers tab stage 2) -- see indexer_repo.py.

API keys are write-only over this API: list/get return `api_key_set`
instead of the key itself, and an update with an empty `api_key` keeps
the stored one (so editing a row's name/priority doesn't require
re-entering its key)."""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import indexer_repo
from newznab_client import NewznabClient

router = APIRouter(prefix="/indexers", tags=["indexers"])

_db = None


def init_indexers_router(db):
    global _db
    _db = db


class IndexerBody(BaseModel):
    name: str
    type: str = "newznab"
    url: str
    api_path: str = "/api"
    api_key: str = ""
    categories: str = "6000"
    priority: int = 25
    enabled: bool = True
    minimum_seeders: int = 1          # Torznab only, *arr default
    seed_ratio: Optional[float] = None  # empty = the download client's own limit
    seed_time: Optional[int] = None     # minutes, empty = the download client's own limit


def _public(row):
    row = dict(row)
    row["api_key_set"] = bool(row.pop("api_key", ""))
    return row


def _validate(body):
    if body.type not in ("newznab", "torznab"):
        raise HTTPException(400, "type must be 'newznab' or 'torznab'")
    if not body.name.strip() or not body.url.strip():
        raise HTTPException(400, "name and url are required")


@router.get("")
def list_indexers():
    return [_public(r) for r in indexer_repo.list_all(_db)]


@router.post("")
def create_indexer(body: IndexerBody):
    _validate(body)
    indexer_id = indexer_repo.create(_db, **{**body.model_dump(), "enabled": int(body.enabled)})
    return _public(indexer_repo.get_one(_db, indexer_id))


@router.put("/{indexer_id}")
def update_indexer(indexer_id: int, body: IndexerBody):
    if indexer_repo.get_one(_db, indexer_id) is None:
        raise HTTPException(404, "indexer not found")
    _validate(body)
    fields = {**body.model_dump(), "enabled": int(body.enabled)}
    if not body.api_key:
        fields.pop("api_key")  # keep the stored key, see module docstring
    indexer_repo.update(_db, indexer_id, **fields)
    return _public(indexer_repo.get_one(_db, indexer_id))


@router.delete("/{indexer_id}")
def delete_indexer(indexer_id: int):
    indexer_repo.delete(_db, indexer_id)
    return {"ok": True}


@router.post("/{indexer_id}/check")
def check_indexer(indexer_id: int):
    """`t=caps` -- cheapest authenticated Newznab call; a wrong API key
    comes back as a Newznab error element, see newznab_client.py."""
    row = indexer_repo.get_one(_db, indexer_id)
    if row is None:
        raise HTTPException(404, "indexer not found")
    try:
        NewznabClient(row["url"], row["api_key"], row["api_path"]).caps()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(502, str(e))
