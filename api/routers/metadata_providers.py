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
    query_delay: float | str | None = None  # seconds between scan requests; '' = global default
    favorites_interval: float | str | None = None  # hours between favorites imports; '' = the StashDB setting


# Built-in providers: fixed name, default colour (the Calendar's blue / ThePornDB's amber); the colour can be changed with the settings
# `provider_color_stashdb` / `provider_color_theporndb`.
BUILTIN_PROVIDERS = [
    ("stashdb", "StashDB", "#2b6cb0", "https://stashdb.org"),
    ("theporndb", "ThePornDB", "#d69e2e", "https://theporndb.net"),
]
_AUTO_COLORS = ["#2f9e6e", "#a45cc4", "#cf5c78", "#3aa6b9", "#8a9a3b", "#c97b3a", "#5c6bc4", "#c4a43a", "#7a8896", "#b5527a"]


def _auto_color(key, used=()):
    """A palette colour no other provider uses yet (first free one); the key's hash only picks the starting point once all are taken."""
    taken = {c.lower() for c in used}
    for c in _AUTO_COLORS:
        if c.lower() not in taken:
            return c
    return _AUTO_COLORS[sum(ord(c) for c in key) % len(_AUTO_COLORS)]


def _builtin_colors():
    from routers.settings import get_setting_value
    return {key: (get_setting_value(_db, f"provider_color_{key}", "") or "").strip() or default for key, _, default, _ in BUILTIN_PROVIDERS}


def assign_free_color(db, key):
    """A new provider gets a colour no other provider has yet (stored, so it stays when others are added/removed). Also used when a
    stash-box is taken over from Stash (stashbox_sync.py)."""
    global _db
    if _db is None:  # called outside the app (tests, scripts)
        _db = db
    used = list(_builtin_colors().values()) + [r["color"] for r in registry() if r["key"] != key]
    return metadata_provider_repo.update(db, key, color=_auto_color(key, used))


@router.get("/registry")
def registry():
    """Every provider the UI may meet in a release row -- the two built-in ones and the user-added ones -- with name and colour."""
    rows = []
    builtin = _builtin_colors()
    for key, name, _, _ in BUILTIN_PROVIDERS:
        rows.append({"key": key, "name": name, "color": builtin[key], "builtin": True, "enabled": True})
    added = metadata_provider_repo.list_all(_db)
    used = list(builtin.values()) + [(p.get("color") or "").strip() for p in added if (p.get("color") or "").strip()]
    for p in added:
        own = (p.get("color") or "").strip()
        color = own
        if not color:  # legacy rows without a stored colour: first palette colour nobody uses, kept distinct from the others
            color = _auto_color(p["key"], used)
            used.append(color)
        rows.append({"key": p["key"], "name": p["name"], "color": color, "own_color": bool(own),
                     "builtin": False, "enabled": bool(p["enabled"]), "url": p["url"]})
    return rows


@router.get("")
def list_providers():
    return metadata_provider_repo.list_all(_db)


@router.post("")
def create_provider(body: MetadataProviderCreateBody):
    if metadata_provider_repo.get_one(_db, body.key) is not None:
        raise HTTPException(400, f"a provider with key '{body.key}' already exists")
    try:
        created = metadata_provider_repo.create(_db, body.key, body.name, body.url, body.api_key, body.enabled)
        return assign_free_color(_db, body.key) or created
    except metadata_provider_repo.InvalidKeyError as e:
        raise HTTPException(400, str(e))


@router.put("/{key}")
def update_provider(key: str, body: MetadataProviderUpdateBody):
    if metadata_provider_repo.get_one(_db, key) is None:
        raise HTTPException(404, "metadata provider not found")
    try:
        return metadata_provider_repo.update(
            _db, key, name=body.name, url=body.url, api_key=body.api_key, enabled=body.enabled, color=body.color,
            query_delay=body.query_delay, favorites_interval=body.favorites_interval,
        )
    except metadata_provider_repo.InvalidKeyError as e:
        raise HTTPException(400, str(e))


@router.delete("/{key}")
def delete_provider(key: str):
    metadata_provider_repo.delete(_db, key)
    return {"ok": True}
