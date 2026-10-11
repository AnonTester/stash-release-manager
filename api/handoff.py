"""The "send to Whisparr" hand-off action.

Confirm-first UX (per PROGRESS.md's 2026-09-11 decision): the caller
(router) is responsible for requiring an explicit confirm step before
calling this -- this module itself just performs the action once
called, it does not gate on confirmation.

Every hand-off is followed by a defensive reset of the release's
parent Studio/Performer monitored flag back to false, since Whisparr's
own auto-create default for those entities couldn't be verified live
(see PROGRESS.md's "Defensive guard against re-triggering the
Studio/Performer bug"). This is deliberately not "best effort" --
if the reset fails, the exception propagates so the caller/log surfaces
it, rather than silently leaving a studio/performer monitored.
"""
import history
import logging

import release_repo
from indexer_search import _build_queries, parent_studio_names, search_and_grab, RESULT_PROVIDER_LOOKUP_FAILED
from routers.settings import resolve_whisparr_scope_preset
from whisparr_client import MovieAlreadyExistsError, WhisparrLookupEmpty

logger = logging.getLogger(__name__)


def uses_whisparr(db, provider):
    """Whether a release of this provider goes through Whisparr at all
    (2026-09-25). Only StashDB ids can be looked up in Whisparr, so any
    other provider (ThePornDB, generic stash-boxes) never does; StashDB
    releases skip it only under the 'no_whisparr' scope preset."""
    return provider == "stashdb" and resolve_whisparr_scope_preset(db) != "no_whisparr"


def hand_off(db, whisparr_client, provider, scene_stashdb_id, entity_type, entity_stashdb_id,
             whisparr_quality_profile_id, whisparr_vr_quality_profile_id, root_folder_path,
             quality_profile, vr_search_enabled, search_source=None, get_provider_client=None,
             get_stash_client=None):
    """The single "Add to Wishlist" entry point (2026-09-25) -- every
    caller (Releases page, Search page, automatic favorite hand-off)
    goes through here. Whisparr-tracked releases take the unchanged
    send_to_whisparr() path; everything else takes send_to_sidecar(),
    which never touches Whisparr. `get_provider_client(provider)`
    returns that provider's metadata client (needed only by the
    Whisparr-free path, for performer names -- see main.py).
    `get_stash_client` (2026-09-28): enables the "already imported via
    another provider" check, see link_imported_equivalent()."""
    if get_stash_client is not None and link_imported_equivalent(db, provider, scene_stashdb_id, get_stash_client):
        return {"already_sent": False, "grabbed": None, "search_result": "already_in_library",
                "already_in_library": True}
    if uses_whisparr(db, provider):
        return send_to_whisparr(db, whisparr_client, provider, scene_stashdb_id, entity_type, entity_stashdb_id,
                                whisparr_quality_profile_id, whisparr_vr_quality_profile_id, root_folder_path,
                                quality_profile, vr_search_enabled, search_source, get_provider_client)
    return send_to_sidecar(db, provider, scene_stashdb_id, entity_type, entity_stashdb_id,
                           quality_profile, vr_search_enabled, search_source, get_provider_client)


def link_imported_equivalent(db, provider, scene_stashdb_id, get_stash_client):
    """If this scene is already in the library via ANOTHER provider (same
    title, date and studio -- release_repo.find_imported_equivalent()),
    marks it 'imported' instead of searching and adds this provider's id
    to that Stash scene, keeping the existing ones (user, 2026-09-28: "we
    only have the TPDB id -> add the StashDB id"). Returns the matching
    row, or None. Stash errors only skip the linking, never the check."""
    other = release_repo.find_imported_equivalent(db, provider, scene_stashdb_id)
    if other is None:
        return None
    try:
        stash_client = get_stash_client()
        endpoint_of = {"stashdb": stash_client.stashdb_endpoint, "theporndb": stash_client.theporndb_endpoint}
        other_endpoint, my_endpoint = endpoint_of.get(other["provider"]), endpoint_of.get(provider)
        if other_endpoint and my_endpoint:
            for scene_id in stash_client.find_scene_ids_by_stash_id(other_endpoint, other["scene_stashdb_id"]):
                stash_client.add_scene_stash_id(scene_id, my_endpoint, scene_stashdb_id)
                logger.info("linked Stash scene %s to %s id %s too", scene_id, provider, scene_stashdb_id)
    except Exception:
        logger.exception("couldn't add the %s id %s to the library scene", provider, scene_stashdb_id)
    release_repo.set_status_for_scene(db, provider, scene_stashdb_id, "imported")
    history.scene("info", "library", f"Already in the library via {other['provider']} -- linked, not searched",
                  provider, scene_stashdb_id)
    logger.info("%s scene %s (%r) is already in the library via %s scene %s -- marked imported, not searched",
                provider, scene_stashdb_id, other["title"], other["provider"], other["scene_stashdb_id"])
    return other


def send_to_sidecar(db, provider, scene_stashdb_id, entity_type, entity_stashdb_id,
                    quality_profile, vr_search_enabled, search_source, get_provider_client):
    """Whisparr-free hand-off (2026-09-25): puts the release on the
    wishlist (status 'sent_to_whisparr' -- the internal name predates
    this path; the UI calls it "On Wishlist") and runs this sidecar's
    own search+grab once, like the Whisparr path's `_attempt_search()`.
    Unfound releases are retried by main.py's run_indexer_search() the
    same way. Rejected under 'full_whisparr': that preset leaves import
    to Whisparr, which can't handle a release it doesn't track."""
    if resolve_whisparr_scope_preset(db) == "full_whisparr":
        raise ValueError(
            f"Releases from '{provider}' can't be handled while the Whisparr scope is \"Full Whisparr "
            "management\" -- Whisparr can only track StashDB scenes. Pick another preset under Settings."
        )
    release = release_repo.get_release(db, provider, scene_stashdb_id, entity_type, entity_stashdb_id)
    if release is None:
        raise ValueError(f"No release found for scene {scene_stashdb_id} / {entity_type} {entity_stashdb_id}")
    already_sent = release["status"] == "sent_to_whisparr"
    if not already_sent:
        release_repo.set_status_for_scene(db, provider, scene_stashdb_id, "sent_to_whisparr")
    search_outcome = attempt_sidecar_search(db, provider, scene_stashdb_id, release, quality_profile,
                                            vr_search_enabled, search_source, get_provider_client)
    return {
        "already_sent": already_sent, "movie": None,
        "grabbed": search_outcome["grab"], "search_result": search_outcome["result"],
    }


def feed_has_lead(db, provider, scene_stashdb_id, release, search_source):
    """RSS mode (2026-09-30): the feed is searched locally, but a provider lookup costs a StashDB/ThePornDB call PER release. Only look a scene
    up when the feed has at least a lead for it. True for every other search source."""
    if getattr(search_source, "name", "") != "feed":
        return True
    return any(search_source.search(q) for q in
               _build_queries(release["title"], [], release["studio_name"], release["scene_date"],
                              parent_studio_names(db, provider, scene_stashdb_id, release["studio_name"])))


def refresh_scene_from_provider(db, provider, scene_stashdb_id, title, scene_date, get_provider_client, write=True):
    """(title, date, performer_names, ok) as the provider has them NOW (v0.174.0: shared by all search paths -- the Whisparr path used to search the
    stored title, which went stale when StashDB renamed the placeholder "placeholder title" to its real title: six wrong downloads in a row).
    The renamed title/date is stored (unless write=False, the dry run). `ok` is False when the lookup FAILED (timeout, error): the stored values come
    back with no cast and the caller must not search on them (v0.176.0; a scene the provider simply does not know is ok=True). Without a provider
    client there is nothing to check: ok=True."""
    performer_names = []
    if not get_provider_client:
        return title, scene_date, performer_names, True
    try:
        client = get_provider_client(provider)
        if client is None:
            return title, scene_date, performer_names, True
        detail = client.get_scene_detail(scene_stashdb_id)
        performer_names = [p["performer"]["name"] for p in (detail or {}).get("performers") or []
                           if (p.get("performer") or {}).get("name")]
        # The provider's CURRENT title/date (found live: the provider renamed a placeholder title to its real
        # one; searching the stale title grabbed a wrong release twice). Stored too, so lists show it.
        fresh_title, fresh_date = (detail or {}).get("title"), (detail or {}).get("date")
        if fresh_title and (fresh_title != title or (fresh_date and fresh_date != scene_date)):
            logger.info("%s scene %s was renamed upstream: %r -> %r", provider, scene_stashdb_id, title, fresh_title)
            if write:
                release_repo.set_title_for_scene(db, provider, scene_stashdb_id, fresh_title, fresh_date)
                history.scene("info", "metadata", f"Renamed upstream: {title!r} -> {fresh_title!r}",
                              provider, scene_stashdb_id, scene_title=fresh_title)
            title, scene_date = fresh_title, fresh_date or scene_date
    except Exception as e:  # noqa: BLE001
        logger.warning("sidecar hand-off: couldn't check %s scene %s at the provider (%s: %s) -- not searched this time",
                       provider, scene_stashdb_id, type(e).__name__, str(e)[:120])
        return title, scene_date, [], False
    return title, scene_date, performer_names, True


def _lookup_failed(db, provider, scene_stashdb_id):
    """Records why nothing was searched; returns the usual {"result", "grab"} answer."""
    release_repo.set_search_result_for_scene(db, provider, scene_stashdb_id, RESULT_PROVIDER_LOOKUP_FAILED)
    return {"result": RESULT_PROVIDER_LOOKUP_FAILED, "grab": None}


def attempt_sidecar_search(db, provider, scene_stashdb_id, release, quality_profile, vr_search_enabled,
                           search_source, get_provider_client):
    """search_and_grab() without Whisparr (2026-09-25) -- shared by
    send_to_sidecar() and main.py's periodic run_indexer_search().
    Performer names come from the provider itself (Whisparr's
    `performerNames` isn't available here); if that lookup fails, the
    search still runs, the title/date checks just lose the performer
    signal. Best-effort like _attempt_search(): always returns
    {"result", "grab"}, never raises."""
    if not feed_has_lead(db, provider, scene_stashdb_id, release, search_source):
        return {"result": None, "grab": None, "no_lead": True}
    title, scene_date, performer_names, ok = refresh_scene_from_provider(db, provider, scene_stashdb_id, release["title"],
                                                                         release["scene_date"], get_provider_client)
    if not ok:
        return _lookup_failed(db, provider, scene_stashdb_id)
    try:
        return search_and_grab(db, None, None, provider, scene_stashdb_id, title, scene_date,
                               performer_names, quality_profile, is_vr=bool(release["is_vr"]),
                               vr_enabled=vr_search_enabled, search_source=search_source,
                               studio_name=release["studio_name"])
    except Exception:
        logger.exception("sidecar hand-off: search failed for %s scene %s", provider, scene_stashdb_id)
        return {"result": None, "grab": None}


def send_to_whisparr(db, whisparr_client, provider, scene_stashdb_id, entity_type, entity_stashdb_id,
                      whisparr_quality_profile_id, whisparr_vr_quality_profile_id, root_folder_path,
                      quality_profile, vr_search_enabled, search_source=None, get_provider_client=None):
    """StashDB only -- Whisparr's own movie lookup can resolve a StashDB
    scene UUID (`stash:<uuid>` lookup term) but has no equivalent for a
    ThePornDB one yet (still an open upstream feature request, Whisparr
    issue #504, checked 2026-09-11), so there's no working hand-off path
    for a provider='theporndb' release today -- the Releases page itself
    already hides/disables the button for those, this is the defensive
    backend-side guard in case that's ever bypassed. See PROGRESS.md's
    "ThePornDB scan integration" section.

    Milestone 3 (2026-09-19): no longer tells Whisparr to search+grab
    itself by default. Under the 'bookkeeping_only' Whisparr-scope
    preset (today's default, see routers/settings.py's
    resolve_whisparr_scope_preset()/WHISPARR_SCOPE_PRESETS), registers
    the movie via register_existing() (bookkeeping only -- monitored=
    False, searchForMovie=False) then immediately attempts this
    project's OWN indexer_search.search_and_grab() once -- if that finds
    nothing, the periodic re-scan loop (main.py's run_indexer_search())
    retries later, and a manual per-release fallback to Whisparr's own
    automatic search remains available (see fallback_to_whisparr()
    below).

    2026-09-22 (Whisparr-scope presets): under 'full_whisparr' or
    'search_sidecar_import', registers via request_download() instead
    (monitored=True, searchForMovie=True -- Whisparr searches+grabs on
    its own) and does NOT also call search_and_grab() itself, since
    Whisparr is already the one searching for these presets and running
    both would just double up on indexer hits / add its own
    title-mismatch noise on top of Whisparr's. 'full_whisparr' also
    needs file_import_source='whisparr_built_in' (Whisparr's own
    Completed Download Handling does the import too) -- 'search_sidecar_
    import' keeps 'sidecar_sabnzbd' and relies on run_indexer_search()'s
    own grab-detection branch (main.py) to notice Whisparr's grab via
    whisparr_client.find_grabbed_download_id() and feed it into the
    existing SabnzbdSource/file_importer pipeline unmodified. Both of
    these are set by the Settings-page preset selector, not by this
    function.

    A release already at status='sent_to_whisparr' (the "Search now"
    button, not "Add to Wishlist") is a genuine RE-search, not a no-op
    -- fixed 2026-09-19 (v0.41.4) after live-testing found "Search now"
    silently did nothing: this exact early-return predates Milestone 3
    (it was harmless there, since Whisparr's own background wanted/RSS
    search kept retrying regardless of what this endpoint did -- see
    the old `request_download()`'s `monitored=True`), but now that
    Whisparr never auto-searches on its own, skipping straight to
    "already sent, nothing to do" left a "Search now" click doing
    literally nothing, forever, for every release already past its
    first hand-off -- i.e. most of them. Now looks up the already-
    registered movie and retries `search_and_grab()` directly, skipping
    the register_existing()/MovieAlreadyExistsError dance below (not
    needed a second time).
    `whisparr_quality_profile_id`/`whisparr_vr_quality_profile_id` are
    still required here -- Whisparr's own add-movie payload needs SOME
    valid profile id regardless of the fact that this project no longer
    acts on Whisparr's own quality decisions (see indexer_search.py's
    module docstring for why its `rejected`/`rejections` are ignored).
    `quality_profile` is this project's own profile (routers/settings.py's
    resolve_quality_profiles()) used for a non-VR selection.
    `vr_search_enabled` is a plain on/off switch, not a second profile
    -- a VR-flagged release is matched by Whisparr's own dedicated "vr"
    quality tag, never by resolution, so there's nothing profile-shaped
    to select for it beyond "search or don't" (see indexer_search.py's
    module docstring and routers/settings.py's resolve_quality_profiles()
    for the full reasoning, added 2026-09-19 v0.40.2 after this was
    first built as a second profile and found confusing/misleading).
    `search_source` (2026-09-25, was `prowlarr_client`; optional) is
    passed straight through to `_attempt_search()`/`search_and_grab()`
    -- see that function's own docstring and search_sources.py for how
    the explicitly chosen backend replaces Whisparr's own `/release` as
    the candidate source under
    the 'bookkeeping_only' scope preset (the only one that reaches
    `_attempt_search()` at all -- 'full_whisparr'/'search_sidecar_import'
    never call it, see the branches below)."""
    if provider != "stashdb":
        raise ValueError(
            f"Hand-off to Whisparr isn't supported for provider '{provider}' yet "
            "(Whisparr can't look up non-StashDB scene ids)"
        )

    release = release_repo.get_release(db, provider, scene_stashdb_id, entity_type, entity_stashdb_id)
    if release is None:
        raise ValueError(f"No release found for scene {scene_stashdb_id} / {entity_type} {entity_stashdb_id}")

    scope = resolve_whisparr_scope_preset(db)

    if release["status"] == "sent_to_whisparr":
        movie = whisparr_client.find_by_stash_id(scene_stashdb_id)
        if movie is None and scope == "bookkeeping_only":
            # Whisparr never learned this scene (see below) and only does bookkeeping here: "Search now" is the sidecar's own search.
            return send_to_sidecar(db, provider, scene_stashdb_id, entity_type, entity_stashdb_id,
                                   quality_profile, vr_search_enabled, search_source, get_provider_client)
        if movie is None:
            raise RuntimeError(
                f"Scene {scene_stashdb_id} is marked sent_to_whisparr but Whisparr doesn't track it -- "
                "needs manual attention directly in Whisparr"
            )
        if scope in ("full_whisparr", "search_sidecar_import"):
            # Whisparr does its own searching under these presets --
            # "Search now" re-triggers Whisparr's own MoviesSearch
            # instead of our own search_and_grab() (same mechanism as
            # fallback_to_whisparr() below).
            whisparr_client.set_movie_monitored(movie["id"], True)
            whisparr_client.search_movie(movie["id"])
            search_outcome = {"result": None, "grab": None}
        else:
            search_outcome = _attempt_search(db, whisparr_client, movie, provider, scene_stashdb_id, release,
                                              quality_profile, vr_search_enabled, search_source, get_provider_client)
        return {
            "already_sent": True, "movie": movie,
            "grabbed": search_outcome["grab"], "search_result": search_outcome["result"],
        }

    # Whisparr's own bookkeeping profile still needs the VR-specific id
    # when available (2026-09-12, see PROGRESS.md's "Per-content-type
    # Whisparr quality profiles (VR)" section) -- falls back to the
    # normal one if the release is VR but no VR profile has been
    # configured yet. This project's own selection, further down, uses
    # `quality_profile`/`vr_search_enabled` directly instead (no
    # per-content-type swap needed -- see search_and_grab()'s `is_vr`
    # argument).
    effective_whisparr_profile_id = whisparr_quality_profile_id
    if release["is_vr"] and whisparr_vr_quality_profile_id:
        effective_whisparr_profile_id = whisparr_vr_quality_profile_id

    try:
        lookup = whisparr_client.lookup_by_stash_id(scene_stashdb_id)
    except WhisparrLookupEmpty:
        # Found live 2026-10-10: a scene one day old was unknown to Whisparr's metadata -> HTTP 500 on "Add to Wishlist". Where Whisparr is
        # only bookkeeping the sidecar does everything itself, so the release goes on the wishlist without a Whisparr record; under the
        # presets where Whisparr searches it is a clear message instead of a crash.
        if scope != "bookkeeping_only":
            raise ValueError(f"Whisparr has no metadata for scene {scene_stashdb_id} yet (its database lags behind StashDB) -- "
                             "try again in a few days, or switch the Whisparr scope under Settings") from None
        history.scene("info", "wishlist", "Whisparr does not know this scene yet -- on the wishlist without a Whisparr record (the sidecar searches)",
                      provider, scene_stashdb_id)
        return send_to_sidecar(db, provider, scene_stashdb_id, entity_type, entity_stashdb_id,
                               quality_profile, vr_search_enabled, search_source, get_provider_client)
    already_existed = False
    register = (whisparr_client.request_download if scope in ("full_whisparr", "search_sidecar_import")
                else whisparr_client.register_existing)
    try:
        movie = register(lookup, effective_whisparr_profile_id, root_folder_path)
    except MovieAlreadyExistsError:
        already_existed = True
        # Confirmed live 2026-09-13 (user report: "Poolside Foursome
        # Orgy" hand-off failed with an unhandled 500) -- Whisparr
        # already had this exact scene as a movie record, from outside
        # this project's own hand-off flow entirely (this studio's
        # scenes predate this plugin; likely a leftover from Whisparr's
        # own historical Monitored-flag catalog). Our own release row
        # never recorded that, so "add" failed Whisparr's own
        # already-exists validation. Look the existing movie up
        # instead of treating this as a real failure, and bring our own
        # status in line with reality so this doesn't keep failing the
        # same way every time this release is clicked again.
        movie = whisparr_client.find_by_stash_id(scene_stashdb_id)
        if movie is None:
            raise RuntimeError(
                f"Whisparr reports scene {scene_stashdb_id} already added, but it can't be found "
                "by stash id -- may need manual attention directly in Whisparr"
            )
        logger.info("hand-off: scene %s was already a Whisparr movie outside this project's own flow, "
                    "syncing our own status to match", scene_stashdb_id)

    # Recorded *before* the defensive unmonitor reset below, not after
    # (2026-09-13, found while investigating a real "Hand-off failed:
    # HTTP 500" report) -- the movie is already genuinely added/found
    # in Whisparr by this point, so our own status should reflect that
    # regardless of what happens next. The reset step calls Whisparr's
    # Studio/Performer list endpoint, which has no way to filter by a
    # single id server-side (see whisparr_client.py's
    # _find_by_foreign_id() docstring) and can be slow enough to time
    # out -- when it did, this release was stuck showing as still
    # needing hand-off even though Whisparr already had it, because the
    # status write never ran. Retrying would have self-healed via the
    # MovieAlreadyExistsError path anyway, but there's no reason to
    # leave the status wrong in the meantime.
    release_repo.set_status_for_scene(db, provider, scene_stashdb_id, "sent_to_whisparr")

    studio_foreign_id = movie.get("studioForeignId")
    performer_foreign_ids = movie.get("performerForeignIds") or []
    _reset_parent_entities(whisparr_client, studio_foreign_id, performer_foreign_ids)

    if scope in ("full_whisparr", "search_sidecar_import"):
        # Whisparr already searches+grabs on its own for these presets
        # (request_download()'s searchForMovie=True above) -- running
        # our own search_and_grab() too would just double up.
        search_outcome = {"result": None, "grab": None}
    else:
        search_outcome = _attempt_search(db, whisparr_client, movie, provider, scene_stashdb_id, release,
                                          quality_profile, vr_search_enabled, search_source, get_provider_client)

    # `search_result` (the raw RESULT_* code, see indexer_search.py)
    # lets the frontend patch this release row's status label in place
    # after a hand-off instead of needing a full list refresh -- see
    # entity-panel.js's confirmHandoff(), added 2026-09-19 (v0.41.2)
    # after the user reported a full-page-style reload collapsing their
    # open year/quarter groups just to show an updated status text.
    return {
        "already_sent": already_existed, "movie": movie,
        "grabbed": search_outcome["grab"], "search_result": search_outcome["result"],
    }


def fallback_to_whisparr(db, whisparr_client, provider, scene_stashdb_id):
    """Manual per-release fallback to Whisparr's OWN automatic
    search+grab (Milestone 3, 2026-09-19) -- for a release this
    project's own indexer_search hasn't found anything acceptable for
    yet. The movie must already be registered with Whisparr (via
    send_to_whisparr() above) as monitored=False; this flips it to
    monitored=True and re-triggers Whisparr's own MoviesSearch command,
    same mechanism the old automatic request_download()/search_movie()
    flow used, just explicit instead of always-on."""
    if provider != "stashdb":
        raise ValueError(f"Whisparr fallback isn't supported for provider '{provider}' yet")

    movie = whisparr_client.find_by_stash_id(scene_stashdb_id)
    if movie is None:
        raise ValueError(f"Whisparr doesn't track scene {scene_stashdb_id} yet -- send it first")

    whisparr_client.set_movie_monitored(movie["id"], True)
    whisparr_client.search_movie(movie["id"])
    logger.info("whisparr fallback: triggered Whisparr's own automatic search for movie %s (scene %s)",
                movie["id"], scene_stashdb_id)
    return {"movie": movie}


def fix_mismatched_download(db, whisparr_client, provider, scene_stashdb_id, entity_type, entity_stashdb_id,
                             whisparr_quality_profile_id, whisparr_vr_quality_profile_id, root_folder_path,
                             quality_profile, vr_search_enabled, search_source=None):
    """For a scene where Whisparr's `hasFile: true` turned out to point
    at the WRONG content (routers/releases.py's `library_status()`
    cross-checks Whisparr's exact file path against Stash's own scan
    and flags `whisparr_file_mismatch` when it lands on a different
    scene entirely -- see PROGRESS.md's "Boosting Morale"/"Summer Camp
    Rules" investigation, 2026-09-14, for the real case this was built
    from). Whisparr itself has no way to know this happened -- from its
    side the movie is already satisfied, so a plain re-triggered search
    would run in upgrade-only mode and very likely find nothing (the
    wrong file already meets the quality profile). This clears Whisparr's
    state back to "never downloaded" and re-adds the movie fresh, so the
    next search is a normal "wanted" one instead:

    1. Best-effort: find the movie's most recent 'grabbed' history event
       and mark it failed (blocklists that exact release/indexer combo
       so a plain re-search doesn't just grab the identical wrong
       release again). Confirmed live this can legitimately find
       nothing -- a movie whose file arrived via a disk scan / pre-
       existing import rather than a real Whisparr search+grab has no
       grabbed event to blocklist in the first place; that's fine, the
       delete+re-add below still gets a fresh normal search either way.
    2. Delete the movie AND its file from disk (`deleteFiles=True`) --
       `DELETE /moviefile/{id}` alone only drops Whisparr's tracking
       record and leaves the (wrong) file sitting on disk, still
       readable by a future Stash scan and liable to get folded into
       the same wrong scene again.
    3. Re-add via the normal send_to_whisparr() hand-off -- first
       resetting this release's own status off 'sent_to_whisparr' if it
       was already there, since send_to_whisparr() short-circuits on
       that status and would otherwise skip re-adding entirely now that
       the movie we just deleted no longer exists in Whisparr at all."""
    if provider != "stashdb":
        raise ValueError(f"Fixing a mismatched download isn't supported for provider '{provider}'")

    movie = whisparr_client.find_by_stash_id(scene_stashdb_id)
    if movie is None:
        raise ValueError(f"Whisparr doesn't currently track scene {scene_stashdb_id} -- nothing to fix")
    movie_id = movie["id"]

    try:
        history_id = whisparr_client.find_grabbed_history_event(movie_id)
        if history_id is not None:
            whisparr_client.mark_history_failed(history_id)
            logger.info("fix-mismatch: blocklisted grab %s for Whisparr movie %s", history_id, movie_id)
        else:
            logger.info("fix-mismatch: no 'grabbed' history event for Whisparr movie %s, nothing to blocklist",
                        movie_id)
    except Exception:
        logger.exception("fix-mismatch: best-effort blocklist failed for Whisparr movie %s, continuing anyway",
                          movie_id)

    whisparr_client.delete_movie(movie_id, delete_files=True)
    logger.info("fix-mismatch: deleted Whisparr movie %s and its file from disk", movie_id)

    release = release_repo.get_release(db, provider, scene_stashdb_id, entity_type, entity_stashdb_id)
    if release and release["status"] == "sent_to_whisparr":
        release_repo.set_status_for_scene(db, provider, scene_stashdb_id, "backlog")

    return send_to_whisparr(db, whisparr_client, provider, scene_stashdb_id, entity_type, entity_stashdb_id,
                             whisparr_quality_profile_id, whisparr_vr_quality_profile_id, root_folder_path,
                             quality_profile, vr_search_enabled, search_source)


def _attempt_search(db, whisparr_client, movie, provider, scene_stashdb_id, release,
                     quality_profile, vr_search_enabled, search_source=None, get_provider_client=None):
    """Shared by both send_to_whisparr() paths (fresh add, and a
    "Search now" re-search on an already-`sent_to_whisparr` release) --
    best-effort: a search/grab failure here shouldn't fail the whole
    hand-off (the movie is already registered/known in Whisparr either
    way) -- the periodic re-scan loop (main.py's run_indexer_search())
    retries later regardless. Always returns
    `{"result": ..., "grab": ...}`, same shape as search_and_grab()
    itself, even on an exception (both None) so callers never need a
    None-check."""
    # v0.174.1: the provider's CURRENT title / date / cast, like the periodic search (without a provider client the stored values stay)
    title, scene_date, performers, ok = refresh_scene_from_provider(db, provider, scene_stashdb_id, release["title"], release["scene_date"],
                                                                    get_provider_client)
    if not ok:
        return _lookup_failed(db, provider, scene_stashdb_id)
    try:
        return search_and_grab(db, whisparr_client, movie["id"], provider, scene_stashdb_id,
                                title, scene_date, performers or movie.get("performerNames"),
                                quality_profile, is_vr=bool(release["is_vr"]), vr_enabled=vr_search_enabled,
                                search_source=search_source, studio_name=release["studio_name"])
    except Exception:
        logger.exception("hand-off: indexer search failed for scene %s, will retry via the periodic re-scan",
                          scene_stashdb_id)
        return {"result": None, "grab": None}


def _reset_parent_entities(whisparr_client, studio_foreign_id, performer_foreign_ids):
    """force_unmonitor_studio()/force_unmonitor_performer() now verify
    each write with a fresh GET (see whisparr_client.py's
    _unmonitor_each() -- ported from a proven-live fix after Whisparr's
    bulk /editor endpoint was found to silently fail to persist
    moviesMonitored:false, see PROGRESS.md). Raising here on any
    "failed" id is what keeps this module's docstring promise ("not
    best effort") true against that same failure mode, not just
    against an outright request exception."""
    if studio_foreign_id:
        studio = whisparr_client.find_studio_by_foreign_id(studio_foreign_id)
        if studio:
            result = whisparr_client.force_unmonitor_studio([studio["id"]])
            if result["failed"]:
                raise RuntimeError(
                    f"failed to clear monitored/moviesMonitored on studio {studio['title']} ({studio['id']}) "
                    "after hand-off -- still monitored, needs manual attention"
                )
            logger.info("reset studio %s (%s) monitored -> false after hand-off", studio["title"], studio["id"])

    performer_ids = []
    for foreign_id in performer_foreign_ids:
        performer = whisparr_client.find_performer_by_foreign_id(foreign_id)
        if performer:
            performer_ids.append(performer["id"])
    if performer_ids:
        result = whisparr_client.force_unmonitor_performer(performer_ids)
        if result["failed"]:
            raise RuntimeError(
                f"failed to clear monitored/moviesMonitored on performer id(s) {result['failed']} "
                "after hand-off -- still monitored, needs manual attention"
            )
        logger.info("reset %d performer(s) monitored -> false after hand-off", len(performer_ids))
