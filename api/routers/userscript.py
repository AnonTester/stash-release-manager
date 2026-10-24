"""Backend of the browser userscript (v0.233.0): buttons on a stash-box's scene pages -- Send to wishlist, Send and search, Open in Stash,
Copy Stash-ID -- plus a status colour per scene. The script is served by this sidecar (`/userscript/srm.user.js`, its own address filled in),
and it only sends a scene id: the scene is fetched here and goes through the same hand-off as the Search page's Add."""
import os
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

import metadata_provider_repo
import release_repo
import watchlist_repo
import watchlist_source
from routers import search as search_router
from routers import watchlist as watchlist_router

router = APIRouter(prefix="/userscript", tags=["userscript"])

_db = None
_get_stash_client = None
_get_stashdb_client = None
_get_stash_url = None

_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "userscript", "srm.user.js")


def init_userscript_router(db, get_stash_client, get_stashdb_client, get_stash_url):
    global _db, _get_stash_client, _get_stashdb_client, _get_stash_url
    _db, _get_stash_client, _get_stashdb_client, _get_stash_url = db, get_stash_client, get_stashdb_client, get_stash_url


def _host(url):
    return (urlparse(str(url or "")).hostname or "").lower()


def provider_for_origin(origin):
    """'stashdb' for stashdb.org, else the key of the enabled stash-box added in Settings whose address has this host; None if unknown."""
    host = _host(origin)
    if not host:
        return None
    if host == "stashdb.org":
        return "stashdb"
    for row in metadata_provider_repo.list_all(_db, enabled_only=True):
        if _host(row["url"]) == host:
            return row["key"]
    return None


def _provider(origin):
    provider = provider_for_origin(origin)
    if provider is None:
        raise HTTPException(404, f"No configured provider for {origin}")
    return provider


@router.get("/state")
def state(origin: str, ids: str):
    """{provider, stash_url, scenes: {id: {status, stash_scene_id}}}. status: in_library (Stash has a file, whatever the release table says),
    else the wishlist state (sent_to_whisparr = on the wishlist, imported, import_failed, dismissed, known = found by a scan but not sent yet), else null."""
    provider = _provider(origin)
    wanted = [i for i in ids.split(",") if i.strip()][:200]
    in_library = search_router._ids_in_library(provider, wanted)
    states = release_repo.wishlist_states(_db, provider, wanted, include_open=True)
    scenes = {i: {"status": "in_library" if i in in_library else states.get(i), "stash_scene_id": in_library.get(i), "srm": states.get(i)}
              for i in wanted}
    return {"provider": provider, "stash_url": (_get_stash_url() or "").rstrip("/"), "scenes": scenes}


@router.post("/wishlist")
def wishlist(body: dict, search: bool = False):
    """Put one scene on the wishlist (search=true: and search for it right now)."""
    provider = _provider(body.get("origin"))
    scene_id = str(body.get("id") or "").strip()
    if not scene_id:
        raise HTTPException(400, "No scene id")
    client = search_router._client_for(provider)
    if not hasattr(client, "get_scene_full"):
        raise HTTPException(400, f"Sending a scene from {provider} is not supported yet")
    try:
        scene = client.get_scene_full(scene_id)
    except Exception as e:
        raise HTTPException(502, f"{provider} lookup failed: {e}")
    if not scene:
        raise HTTPException(404, "The provider does not know this scene")
    return search_router.handoff_scene(provider, scene, search)


def _kind(kind):
    if kind not in ("performer", "studio"):
        raise HTTPException(400, "kind must be 'performer' or 'studio'")
    return kind


def _find_local(kind, provider, entity_id):
    stash_client = _get_stash_client()
    endpoint = search_router._endpoint_for(provider, stash_client)
    find = stash_client.find_studio_by_stash_id if kind == "studio" else stash_client.find_performer_by_stash_id
    return stash_client, find(entity_id, endpoint)


@router.get("/entity-state")
def entity_state(kind: str, id: str, origin: str = "", provider: str = ""):
    """One performer / studio of a stash-box page: does Stash have it (`local_id`) and is it on the watchlist (v0.234.0)? The watchlist
    follows the Favorite flag or the watchlist tag, see watchlist_source.py."""
    kind = _kind(kind)
    provider = provider or _provider(origin)  # the plugin's Search page names the provider itself (v0.240.20)
    try:
        stash_client, local = _find_local(kind, provider, id)
        watched = False
        if local:
            watched, _state = watchlist_source.is_watched(_db, stash_client, kind, int(local["id"]))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Stash lookup failed: {e}")
    return {"provider": provider, "kind": kind, "local_id": local["id"] if local else None, "watched": bool(watched),
            "mode": watchlist_source.get_mode(_db), "tag_name": watchlist_source.get_tag_name(_db),
            "stash_url": (_get_stash_url() or "").rstrip("/")}


@router.post("/watch")
def watch(body: dict):
    """Put a performer / studio on the watchlist (created in Stash first when Stash does not have it) or take it off. Taking it off only
    clears the watchlist mark (Favorite flag or tag) -- nothing is deleted in Stash."""
    kind = _kind(body.get("kind"))
    provider = body.get("provider") or _provider(body.get("origin"))
    entity_id = str(body.get("id") or "").strip()
    if not entity_id:
        raise HTTPException(400, "No id")
    if body.get("watched", True):
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "No name to create the entry in Stash with")
        make = search_router.favorite_studio if kind == "studio" else search_router.favorite_performer
        result = make(provider, entity_id, {"name": name})
        watchlist_router._on_watch_changed(kind, int(result["id"]), True)
        # The watchlist rows of this program are made from Stash at the next scan's sync; the entry the user just watched is added at once, so the
        # Watchlist page shows it now (v0.240.22). ThePornDB rows need a lookup of their native id: those stay with the sync.
        if provider != "theporndb":
            watchlist_repo.add_to_watchlist(_db, provider, kind, entity_id, name, local_id=int(result["id"]))
        return {"ok": True, "local_id": result["id"], "watched": True, "created": result.get("created", False)}
    _stash_client, local = _find_local(kind, provider, entity_id)
    if not local:
        return {"ok": True, "local_id": None, "watched": False}
    watchlist_router.set_watch_state(kind, int(local["id"]), watchlist_router.WatchStateBody(watched=False))
    return {"ok": True, "local_id": local["id"], "watched": False}


@router.get("/srm.user.js")
def script(request: Request):
    with open(_SCRIPT, encoding="utf-8") as f:
        text = f.read()
    base = str(request.base_url).rstrip("/")
    text = text.replace("__SRM_URL__", base).replace("__SRM_HOST__", request.url.hostname or "")
    return Response(text, media_type="text/javascript; charset=utf-8",
                    headers={"Cache-Control": "no-cache"})
