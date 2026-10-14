"""Connection health-check for the external services this sidecar talks
to (Stash, StashDB, Whisparr, ThePornDB, Prowlarr, SABnzbd, plus any
additional generic Metadata Providers) -- backs the Releases page's
"Check connections" button. Each provider's client is built fresh per
call via the factory functions passed in from main.py, same reasoning
as get_stashdb_client() there: URL/key can be edited on the Settings
page at any time, so a stale cached client would give a misleading
result right after a save."""
from fastapi import APIRouter

router = APIRouter(prefix="/connections", tags=["connections"])

_factories = None
_get_dynamic_factories = None


def init_connections_router(factories, get_dynamic_factories=None):
    """factories: dict of provider name -> zero-arg callable returning a
    client with a .ping() method, for the fixed set of connections known
    at startup.

    `get_dynamic_factories` (Metadata Provider generalization,
    2026-09-21): an optional zero-arg callable returning the SAME shape
    of dict, called fresh on every /check -- unlike `factories`, an
    additional generic stash-box provider can be added/removed at
    runtime (see metadata_provider_repo.py), so its factory can't be
    baked in once at startup the way the fixed connections are."""
    global _factories, _get_dynamic_factories
    _factories = factories
    _get_dynamic_factories = get_dynamic_factories


def _all_factories():
    if not _get_dynamic_factories:
        return _factories
    return {**_factories, **_get_dynamic_factories()}


@router.get("/check")
def check(only: str = None):
    """`only` (comma separated provider names) limits the check -- the page header's live Stash indicator asks for just `stash`."""
    wanted = {n.strip() for n in only.split(",")} if only else None
    results = {}
    for name, make_client in _all_factories().items():
        if wanted is not None and name not in wanted:
            continue
        try:
            make_client().ping()
            results[name] = {"ok": True, "error": None}
        except Exception as e:
            msg = str(e)
            if "No scheme supplied" in msg:  # requests' wording for an empty / missing address
                msg = "no address saved yet -- enter the URL (and the API key) and save first"
            results[name] = {"ok": False, "error": msg}
    return results


@router.get("/whisparr/quality-profiles")
def whisparr_quality_profiles():
    """Backs the Settings page's quality-profile dropdowns -- see
    whisparr_client.py's get_quality_profiles() docstring."""
    return _factories["whisparr"]().get_quality_profiles()


@router.get("/sabnzbd/history")
def sabnzbd_history(search: str = None, limit: int = 50):
    """Backs the Settings page's SABnzbd diagnostic panel -- added
    2026-09-19 for the Milestone 2 cutover investigation (see
    sabnzbd_client.py's module docstring). `storage` on each returned
    entry is the actual final path SABnzbd wrote the completed job to."""
    return _factories["sabnzbd"]().get_history(search=search, limit=limit)


@router.get("/sabnzbd/queue")
def sabnzbd_queue():
    return _factories["sabnzbd"]().get_queue()
