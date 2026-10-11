"""Ad-hoc search against a configured metadata provider (2026-09-19,
see PROGRESS.md/roadmap memory) -- backs the plugin's own Search page,
which replaces the previous workflow of searching StashDB.org's own
website and handing results to Stash/Whisparr via the separate
Stasharr browser extension. Unlike Whisparr's own search (locked to
whichever single stash-box endpoint Whisparr itself is configured
against), this works against whichever provider this project has
configured -- `provider` is 'stashdb' or 'theporndb' throughout, same
convention as everywhere else in this project.

Two actions once something's found:
- Performer/Studio: "Favorite" -- creates the local Stash entity if it
  doesn't exist yet (or just flips its favorite flag if it does), via
  stash_client.py's create_or_favorite_performer()/create_or_favorite_studio().
  The existing Favorite-driven watchlist sync (sync.py) picks it up
  automatically before the next scan; nothing else to wire here.
- Scene: "Send to Whisparr" -- StashDB only (see handoff.py's
  send_to_whisparr() docstring; ThePornDB has no Whisparr-side lookup
  path). Registers a release row for the scene first (exactly as if a
  scan had found it -- picks the scene's studio, or failing that its
  first performer, as the "entity" it's recorded against), then reuses
  send_to_whisparr() unchanged, so it shows up in the normal Releases
  list/import-check afterward like any other hand-off.

After Milestone 3, a scene hand-off should go straight to this
sidecar's own download handling instead of via Whisparr -- not
implemented yet, deliberately deferred (see the roadmap memory)."""
import history
from fastapi import APIRouter, HTTPException

import metadata_provider_repo
import release_repo
import watchlist_source
from handoff import hand_off
from routers.settings import resolve_whisparr_quality_profiles, resolve_quality_profiles

router = APIRouter(prefix="/search", tags=["search"])

_db = None
_get_stashdb_client = None
_get_theporndb_client = None
_get_stash_client = None
_get_whisparr_client = None
_get_search_source = None
_get_provider_client = None
_settings = None


def init_search_router(db, get_stashdb_client, get_theporndb_client, get_stash_client, get_whisparr_client,
                        get_search_source, settings, get_provider_client=None):
    """Factories, not fixed instances -- same reasoning as every other
    router here (a Settings-page connection edit takes effect on the
    very next call). `get_search_source` (2026-09-25, was
    get_prowlarr_client_if_configured) -- see main.py's own docstring --
    passed straight through to handoff_scene()'s send_to_whisparr()
    call below."""
    global _db, _get_stashdb_client, _get_theporndb_client, _get_stash_client, _get_whisparr_client
    global _get_search_source, _settings, _get_provider_client
    _get_provider_client = get_provider_client
    _db, _get_stashdb_client, _get_theporndb_client, _get_stash_client, _get_whisparr_client = (
        db, get_stashdb_client, get_theporndb_client, get_stash_client, get_whisparr_client
    )
    _get_search_source, _settings = get_search_source, settings


def _client_for(provider):
    if provider == "stashdb":
        return _get_stashdb_client()
    if provider == "theporndb":
        return _get_theporndb_client()
    # A stash-box added in Settings > Connections (v0.157.0): same client class as StashDB, built from its own URL / key.
    client = _get_provider_client(provider) if _get_provider_client else None
    if client is None:
        raise HTTPException(400, f"Unknown or disabled provider '{provider}'")
    return client


def _endpoint_for(provider, stash_client):
    """The stash-box endpoint a local Stash entity is tagged with for this provider: StashDB / ThePornDB as configured in Stash, an added
    provider by its own URL (the same value the watchlist sync matches on, see sync.py)."""
    if provider in ("stashdb", "theporndb"):
        return stash_client.endpoint_for_provider(provider)
    row = metadata_provider_repo.get_one(_db, provider)
    if row is None:
        raise HTTPException(400, f"Unknown provider '{provider}'")
    return row["url"]


def _reraise_as_http(e, provider):
    # Surfaces a provider-side rejection (bad/under-privileged API key,
    # account tier without access to this particular search -- see
    # PROGRESS.md's 2026-09-19 investigation, which turned out to be a
    # blank test key rather than a real limitation, but the failure
    # mode itself is real and worth a clear message) instead of a bare
    # 500 with a stack trace the Search page can't do anything useful
    # with.
    raise HTTPException(502, f"{provider} search failed: {e}")


@router.get("/{provider}/performers")
def search_performers(provider: str, term: str):
    client = _client_for(provider)
    try:
        return client.search_performers(term)
    except Exception as e:
        _reraise_as_http(e, provider)


@router.get("/{provider}/studios")
def search_studios(provider: str, term: str):
    client = _client_for(provider)
    try:
        return client.search_studios(term)
    except Exception as e:
        _reraise_as_http(e, provider)


@router.get("/{provider}/scenes")
def search_scenes(provider: str, term: str):
    client = _client_for(provider)
    try:
        return client.search_scenes(term)
    except Exception as e:
        _reraise_as_http(e, provider)


@router.post("/{provider}/performers/{stashdb_id}/favorite")
def favorite_performer(provider: str, stashdb_id: str, body: dict):
    stash_client = _get_stash_client()
    endpoint = _endpoint_for(provider, stash_client)
    # 'Watch' (2026-09-26): tag mode puts the watchlist tag on it instead
    # of the Favorite flag, see watchlist_source.py.
    watch_tag_id = watchlist_source.watch_tag_id_for_new_entity(_db, stash_client)
    return {**stash_client.create_or_favorite_performer(stashdb_id, body.get("name", ""), endpoint, watch_tag_id),
            "watch_mode": watchlist_source.get_mode(_db)}


@router.post("/{provider}/studios/{stashdb_id}/favorite")
def favorite_studio(provider: str, stashdb_id: str, body: dict):
    stash_client = _get_stash_client()
    endpoint = _endpoint_for(provider, stash_client)
    watch_tag_id = watchlist_source.watch_tag_id_for_new_entity(_db, stash_client)
    return {**stash_client.create_or_favorite_studio(stashdb_id, body.get("name", ""), endpoint, watch_tag_id),
            "watch_mode": watchlist_source.get_mode(_db)}


@router.post("/{provider}/scenes/handoff")
def handoff_scene(provider: str, body: dict):
    """`body` is one full scene dict exactly as returned by
    search_scenes() above -- the frontend already has it from the
    search results, no need to re-fetch it server-side. Any provider
    since 2026-09-25 -- non-StashDB scenes take the Whisparr-free path,
    see handoff.hand_off()."""

    studio = body.get("studio") or {}
    performers = body.get("performers") or []
    if studio.get("id"):
        entity_type, entity_stashdb_id = "studio", studio["id"]
    elif performers and performers[0].get("id"):
        entity_type, entity_stashdb_id = "performer", performers[0]["id"]
    else:
        raise HTTPException(400, "This scene has no studio or performer to record the hand-off against")

    with _db.connect() as conn:
        release_repo.insert_release(_db, conn, body, provider, entity_type, entity_stashdb_id, status="new")

    whisparr_quality_profile_id, whisparr_vr_quality_profile_id = resolve_whisparr_quality_profiles(_db)
    quality_profile, vr_search_enabled = resolve_quality_profiles(_db)
    try:
        result = hand_off(
            _db, _get_whisparr_client(), provider, body["id"], entity_type, entity_stashdb_id,
            whisparr_quality_profile_id, whisparr_vr_quality_profile_id, _settings.whisparr_root_folder_path,
            quality_profile, vr_search_enabled, search_source=_get_search_source(),
            get_provider_client=_get_provider_client, get_stash_client=_get_stash_client,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    if not result.get("already_in_library"):
        history.scene("info", "wishlist", "Added to the wishlist (Search page)", provider, body["id"],
                      scene_title=body.get("title"))
    return result
