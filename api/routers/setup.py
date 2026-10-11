"""First-start setup status (v0.179.0): which of the things a new installation needs are entered. Computed from the real state every time --
deliberately NOT a stored "setup done" flag: a flag would travel with an export / import and the next installation would skip its setup
although it has no keys yet. One local request, no calls to other services."""
from fastapi import APIRouter

import download_client_repo
import indexer_repo
import quality_profile_repo
from routers.settings import get_effective_connections, get_setting_value

router = APIRouter(prefix="/setup", tags=["setup"])

_db = None


def init_setup_router(db):
    global _db
    _db = db


@router.get("/status")
def status():
    eff = get_effective_connections()
    clients = [c for c in download_client_repo.list_all(_db) if c.get("enabled")]
    indexers = indexer_repo.list_enabled(_db)
    profiles = quality_profile_repo.list_all(_db)
    prowlarr = bool(eff["prowlarr_url"]) and eff["prowlarr_api_key_set"]
    steps = [
        {"key": "stash", "label": "Stash", "required": True, "done": bool(eff["stash_url"]),
         "detail": "" if eff["stash_url"] else "Stash URL and API key go into the plugin's settings in Stash"},
        {"key": "stashdb", "label": "StashDB", "required": True, "done": bool(eff["stashdb_api_key_set"]),
         "detail": "" if eff["stashdb_api_key_set"] else "StashDB API key missing"},
        {"key": "theporndb", "label": "ThePornDB", "required": False, "done": bool(eff["theporndb_api_key_set"]),
         "detail": "optional"},
        {"key": "search", "label": "Search", "required": True, "done": prowlarr or bool(indexers),
         "detail": f"{len(indexers)} indexer(s)" + (", Prowlarr" if prowlarr else "") if (prowlarr or indexers) else "no Prowlarr and no indexer"},
        {"key": "download", "label": "Downloads", "required": True, "done": bool(clients),
         "detail": ", ".join(c["name"] for c in clients) if clients else "no download client"},
        {"key": "quality", "label": "Quality", "required": True, "done": bool(profiles),
         "detail": ", ".join(p["name"] for p in profiles) if profiles else "no quality profile"},
        {"key": "content", "label": "Content & watchlist", "required": False, "done": True,
         "detail": f"content: {get_setting_value(_db, 'content_scope', 'both')}"},
        {"key": "whisparr", "label": "Whisparr", "required": False,
         "done": bool(eff["whisparr_url"]) and eff["whisparr_api_key_set"], "detail": "optional"},
    ]
    return {"steps": steps, "complete": all(s["done"] for s in steps if s["required"]),
            "missing": [s["label"] for s in steps if s["required"] and not s["done"]]}
