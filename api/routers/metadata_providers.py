"""CRUD endpoints for ADDITIONAL generic stash-box metadata providers
(Metadata Provider generalization, 2026-09-21) -- see
metadata_provider_repo.py for why StashDB/ThePornDB are NOT managed
here."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import metadata_provider_repo

router = APIRouter(prefix="/metadata-providers", tags=["metadata-providers"])

_db = None


def init_metadata_providers_router(db):
    global _db
    _db = db


class MetadataProviderCreateBody(BaseModel):
    key: str
    name: str
    url: str
    api_key: str = ""
    enabled: bool = True


class MetadataProviderUpdateBody(BaseModel):
    name: str | None = None
    url: str | None = None
    api_key: str | None = None
    enabled: bool | None = None
    color: str | None = None


# Built-in providers: fixed name, default colour (the Calendar's blue / ThePornDB's amber); the colour can be changed with the settings
# `provider_color_stashdb` / `provider_color_theporndb`.
BUILTIN_PROVIDERS = [
    ("stashdb", "StashDB", "#2b6cb0", "https://stashdb.org"),
    ("theporndb", "ThePornDB", "#d69e2e", "https://theporndb.net"),
]
_AUTO_COLORS = ["#2f9e6e", "#a45cc4", "#cf5c78", "#3aa6b9", "#8a9a3b", "#c97b3a"]


def _auto_color(key):
    return _AUTO_COLORS[sum(ord(c) for c in key) % len(_AUTO_COLORS)]


@router.get("/registry")
def registry():
    """Every provider the UI may meet in a release row -- the two built-in ones and the user-added ones -- with name and colour."""
    from routers.settings import get_setting_value
    rows = []
    for key, name, default, _ in BUILTIN_PROVIDERS:
        color = (get_setting_value(_db, f"provider_color_{key}", "") or "").strip() or default
        rows.append({"key": key, "name": name, "color": color, "builtin": True, "enabled": True})
    for p in metadata_provider_repo.list_all(_db):
        rows.append({"key": p["key"], "name": p["name"], "color": (p.get("color") or "").strip() or _auto_color(p["key"]),
                     "own_color": bool((p.get("color") or "").strip()), "builtin": False, "enabled": bool(p["enabled"]), "url": p["url"]})
    return rows


@router.get("")
def list_providers():
    return metadata_provider_repo.list_all(_db)


@router.post("")
def create_provider(body: MetadataProviderCreateBody):
    if metadata_provider_repo.get_one(_db, body.key) is not None:
        raise HTTPException(400, f"a provider with key '{body.key}' already exists")
    try:
        return metadata_provider_repo.create(_db, body.key, body.name, body.url, body.api_key, body.enabled)
    except metadata_provider_repo.InvalidKeyError as e:
        raise HTTPException(400, str(e))


@router.put("/{key}")
def update_provider(key: str, body: MetadataProviderUpdateBody):
    if metadata_provider_repo.get_one(_db, key) is None:
        raise HTTPException(404, "metadata provider not found")
    try:
        return metadata_provider_repo.update(
            _db, key, name=body.name, url=body.url, api_key=body.api_key, enabled=body.enabled, color=body.color,
        )
    except metadata_provider_repo.InvalidKeyError as e:
        raise HTTPException(400, str(e))


@router.delete("/{key}")
def delete_provider(key: str):
    metadata_provider_repo.delete(_db, key)
    return {"ok": True}
