"""Scan orchestration: for every watched studio/performer, fetch new
scenes since its watermark and persist them as releases. Ported/
generalized from an earlier standalone script's main loop.

Extended 2026-09-11 to scan both providers (StashDB and ThePornDB, see
PROGRESS.md's "ThePornDB scan integration" section) -- each watchlist
entry already carries which provider it belongs to (see db.py's
schema), so this just dispatches to the matching client per entry
rather than assuming StashDB everywhere. The two clients are
duck-type-compatible for what this function needs
(fetch_newest_scene_created / fetch_new_scenes with the same normalized
scene-dict shape), so the dispatch loop itself doesn't need to know
anything provider-specific beyond picking the right client and id
(`native_id` for ThePornDB entries, `stashdb_id` for StashDB ones --
see sync.py's docstring for why those differ)."""
import history
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import release_repo
import watchlist_repo
from stashdb_client import FeedIncomplete

logger = logging.getLogger(__name__)


def run_scan(db, clients, delay=0.3, progress_callback=None, auto_handoff_callback=None, entries=None):
    """Scans the whole watchlist. `clients` is {"stashdb": ..., "theporndb": ...}.
    Returns a summary dict. First run for any given entity only
    establishes a baseline (its current newest scene) and reports
    nothing for it -- otherwise every entity's entire catalog would show
    up as "new" the first time, same reasoning as release_watcher.py.

    `progress_callback(current, total, entity_name)`, if given, is
    called once at the *start* of each entity's processing (1-indexed,
    so the last call has current == total right as that entity begins)
    -- added 2026-09-12 so a live status endpoint can report how far
    into a long run the scan actually is, see main.py's use of this and
    PROGRESS.md's "Scan progress counter" section. A full run can take
    hours against a large watchlist (2000+ entities observed), with no
    other way from outside the process to tell "almost done" from
    "barely started".

    `auto_handoff_callback(entity_type, entity_stashdb_id, scene_stashdb_id)`,
    if given, is called for each newly-inserted release that belongs to
    an entity with `auto_handoff` enabled (see watchlist_repo.py's
    set_auto_handoff(), 2026-09-13, PROGRESS.md's "Auto hand-off to
    Whisparr, per entity" section) and whose scene date falls inside
    that entity's optional from/until window. Kept as a callback rather
    than calling handoff.send_to_whisparr() directly here -- this
    module has no Whisparr client/quality-profile settings of its own
    (same reasoning as progress_callback being main.py's concern, not
    this one's). A callback exception is logged and doesn't interrupt
    the scan -- one failed auto hand-off shouldn't lose the rest of the
    run; the release stays 'new' and can still be sent manually."""
    watched = watchlist_repo.list_watchlist(db) if entries is None else entries
    total = len(watched)
    summary = {"scanned": 0, "new_baselines": 0, "new_releases": 0, "failed": []}

    for i, entry in enumerate(watched):
        if progress_callback:
            progress_callback(i + 1, total, entry["name"])
        provider = entry["provider"]
        entity_type = entry["entity_type"]
        stashdb_id = entry["stashdb_id"]
        # For a theporndb entry, `stashdb_id` is only the stash-box-shim
        # UUID used for watchlist membership tracking (see sync.py) --
        # the client needs the resolved numeric native_id instead. A
        # missing native_id here means a still-in-flight or previously
        # failed resolution (see sync.py's resolve_failed reporting);
        # skip it this scan rather than error, it'll resolve on a future
        # sync.
        query_id = entry["native_id"] if provider == "theporndb" else stashdb_id
        if provider == "theporndb" and not query_id:
            continue
        client = clients[provider]
        # an added stash-box may have its own request delay (Settings > Metadata providers); StashDB/ThePornDB use the global one
        entry_delay = delay if provider in ("stashdb", "theporndb") else getattr(client, "delay", delay)

        since = watchlist_repo.get_watermark(db, provider, entity_type, stashdb_id)

        # Baseline case (no watermark yet) only ever needs the single
        # newest scene's `created` timestamp -- a dedicated one-page
        # request instead of list(fetch_new_scenes(...)), which would
        # drain the entity's *entire* catalog via full pagination just
        # to read scenes[0]. Confirmed live 2026-09-11: this was the
        # real cause of a scan silently stalling for many minutes on
        # StashDB.org's real (often much larger) favorited catalogs --
        # see PROGRESS.md's "Missing inter-page delay" /
        # "Baseline pagination cost" sections.
        if since is None:
            try:
                newest = client.fetch_newest_scene_created(entity_type, query_id)
            except Exception as e:
                logger.warning("scan failed for %s %s %s (%s): %s",
                                provider, entity_type, entry["name"], stashdb_id, e)
                summary["failed"].append({"provider": provider, "entity_type": entity_type,
                                           "name": entry["name"], "error": str(e)})
                time.sleep(entry_delay)
                continue
            summary["scanned"] += 1
            if newest:
                watchlist_repo.set_watermark(db, provider, entity_type, stashdb_id, newest)
                summary["new_baselines"] += 1
            time.sleep(entry_delay)
            continue

        try:
            scenes = list(client.fetch_new_scenes(entity_type, query_id, since))
        except Exception as e:
            logger.warning("scan failed for %s %s %s (%s): %s",
                            provider, entity_type, entry["name"], stashdb_id, e)
            summary["failed"].append({"provider": provider, "entity_type": entity_type,
                                       "name": entry["name"], "error": str(e)})
            time.sleep(entry_delay)
            continue

        summary["scanned"] += 1
        _store_new_scenes(db, entry, scenes, auto_handoff_callback, summary)

        time.sleep(entry_delay)

    return summary


# First feed run for a provider (no feed watermark yet): start this far
# back -- overlaps the last per-entry scan (6h interval) with margin;
# anything already seen is filtered by the per-entry watermarks.
_FEED_INITIAL_LOOKBACK = timedelta(hours=24)


# After a clean per-entry fallback the feed starts again from here (a margin behind "now": the next feed read overlaps it, and a scene
# is only stored when it is newer than its entry's own watermark, so nothing is stored twice).
_FEED_FALLBACK_MARGIN = timedelta(minutes=30)


def _advance_feed_watermark(db, provider):
    """The per-entry scan just covered every watched entry up to now: move the provider's feed watermark forward, so the next feed read is
    short again instead of starting where it first failed. Never moves it backwards."""
    new = (datetime.now(timezone.utc) - _FEED_FALLBACK_MARGIN).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    current = watchlist_repo.get_feed_watermark(db, provider)
    if current is None or current < new:
        watchlist_repo.set_feed_watermark(db, provider, new)
        logger.info("feed scan: %s feed watermark advanced %s -> %s after a clean per-entry scan", provider, current, new)


def run_feed_scan(db, clients, delay=0.3, progress_callback=None, auto_handoff_callback=None):
    """The "Neuheiten-Feed" scan (2026-09-25, top of the user's ToDo):
    instead of one request per watchlist entry (~2277, 1h+), read each
    provider's global "recently created" feed since its feed watermark
    (measured live: StashDB ~2 pages, ThePornDB ~22 pages per 6h) and
    match scenes to watched studios/performers locally. Stores through
    the same _store_new_scenes() as run_scan(), and only scenes newer
    than the entry's own watermark -- so overlapping windows or a
    switch between modes never duplicate anything.

    Falls back to run_scan() (per-entry) for:
    - entries without a watermark yet (their first-run baseline), and
    - a whole provider whose feed couldn't reach back to its feed
      watermark (FeedIncomplete -- e.g. ThePornDB's 10000-result cap
      after the sidecar was down for more than ~a day).
    - a feed request that fails (timeout on a deep page, v0.236.0), same fallback. A clean fallback advances the feed watermark
      (`_advance_feed_watermark`); a fallback with failures keeps it, so the next run tries the same window again. Returns run_scan()'s summary
    shape plus a per-provider `feed` report."""
    watched = watchlist_repo.list_watchlist(db)
    summary = {"scanned": 0, "new_baselines": 0, "new_releases": 0, "failed": [], "feed": {}}

    def merge(sub):
        for k in ("scanned", "new_baselines", "new_releases"):
            summary[k] += sub[k]
        summary["failed"].extend(sub["failed"])

    by_provider = defaultdict(list)
    for entry in watched:
        by_provider[entry["provider"]].append(entry)
    # StashDB first: cross-provider dedup lets StashDB win either way,
    # but this keeps the replace-a-sibling churn minimal.
    providers = sorted(by_provider, key=lambda p: (p != "stashdb", p))

    for idx, provider in enumerate(providers):
        if progress_callback:
            progress_callback(idx + 1, len(providers), f"{provider} feed")
        client = clients.get(provider)
        if client is None or not hasattr(client, "fetch_recent_scenes"):
            continue
        entries = [e for e in by_provider[provider] if provider != "theporndb" or e.get("native_id")]
        watermarks = {id(e): watchlist_repo.get_watermark(db, provider, e["entity_type"], e["stashdb_id"])
                      for e in entries}
        baseline = [e for e in entries if watermarks[id(e)] is None]
        active = [e for e in entries if watermarks[id(e)] is not None]
        if baseline:
            merge(run_scan(db, {provider: client}, delay, None, auto_handoff_callback, entries=baseline))

        index = defaultdict(list)
        for e in active:
            match_id = str(e["native_id"]) if provider == "theporndb" else e["stashdb_id"]
            index[(e["entity_type"], match_id)].append(e)

        since = watchlist_repo.get_feed_watermark(db, provider) or (
            datetime.now(timezone.utc) - _FEED_INITIAL_LOOKBACK).strftime("%Y-%m-%dT%H:%M:%SZ")
        matched = defaultdict(list)
        newest, seen = None, 0
        try:
            for scene in client.fetch_recent_scenes(since):
                newest = newest or scene["created"]
                seen += 1
                hits = [e for sid in scene["_studio_ids"] for e in index.get(("studio", sid), [])]
                hits += [e for pid in scene["_performer_ids"] for e in index.get(("performer", pid), [])]
                for e in hits:
                    matched[id(e)].append(scene)
        except Exception as e:
            # FeedIncomplete (the feed could not reach back to the watermark) or a feed request that failed (timeout on a deep page):
            # the watched entries are scanned one by one instead, so nothing is missed (v0.236.0: a failed feed request used to skip
            # the provider altogether, and the watermark -- which a fallback never advanced -- fell further behind every run).
            incomplete = isinstance(e, FeedIncomplete)
            if incomplete:
                logger.warning("feed scan: %s feed incomplete (%s) -- falling back to per-entry scan", provider, e)
                history.event("warning", "job", f"Scan: {provider} feed incomplete, fell back to the per-entry scan",
                              details={"reason": str(e)})
            else:
                logger.exception("feed scan: %s feed failed -- falling back to per-entry scan", provider)
                history.event("warning", "job", f"Scan: {provider} feed could not be read, fell back to the per-entry scan",
                              details={"reason": str(e)})
            summary["feed"][provider] = {"fallback": "per_entry", "reason": str(e)}
            sub = run_scan(db, {provider: client}, delay, None, auto_handoff_callback, entries=active)
            merge(sub)
            if not sub["failed"]:
                _advance_feed_watermark(db, provider)
            continue

        stored = 0
        for e in active:
            fresh = [s for s in matched.get(id(e), []) if s["created"] > watermarks[id(e)]]
            if fresh:
                before = summary["new_releases"]
                # Each entry gets its own scene dict copies: dedup marks
                # scenes (`_carry_dismissed`) per entry.
                _store_new_scenes(db, e, [dict(s) for s in fresh], auto_handoff_callback, summary)
                stored += summary["new_releases"] - before
        summary["scanned"] += len(active)
        if newest:
            watchlist_repo.set_feed_watermark(db, provider, newest)
        summary["feed"][provider] = {"scenes_read": seen, "entries_matched": sum(1 for v in matched.values() if v),
                                     "new_releases": stored, "since": since}
        logger.info("feed scan: %s -- %d scene(s) since %s, %d watched entr(y/ies) matched, %d new release(s)",
                    provider, seen, since, summary["feed"][provider]["entries_matched"], stored)

    return summary


def _store_new_scenes(db, entry, scenes, auto_handoff_callback, summary):
    """Persists one watchlist entry's new scenes (newest first) --
    cross-provider dedup, release insert, carried-over dismissals,
    auto hand-off, watermark. Shared by run_scan() (per-entry queries)
    and run_feed_scan() (global feed, 2026-09-25) so both store exactly
    the same way."""
    if not scenes:
        return
    provider = entry["provider"]
    entity_type = entry["entity_type"]
    stashdb_id = entry["stashdb_id"]
    # Per-entity date window (2026-09-26): scenes released outside it are
    # not stored at all. The watermark below still advances over them
    # (scenes[0] is taken from the unfiltered list).
    window_from, window_until, _ = watchlist_repo.effective_window(db, entry)
    in_scope = [s for s in scenes if watchlist_repo.in_window(s.get("date"), window_from, window_until)]
    skipped = len(scenes) - len(in_scope)
    to_insert = _dedup_against_other_providers(db, provider, entity_type, entry["name"], stashdb_id, in_scope)
    late_days = _late_addition_days(db)
    late_ids = {scene["id"] for scene in to_insert if _is_late_addition(scene, late_days)}
    if to_insert:
        with db.connect() as conn:
            for scene in to_insert:
                late = scene["id"] in late_ids
                release_repo.insert_release(db, conn, scene, provider, entity_type, stashdb_id,
                                            status="backlog" if late else "new", late_addition=late)
        if provider == "stashdb":  # the same scene may already be 'imported' under another entity (promotion above)
            for scene in to_insert:
                release_repo.adopt_imported_status(db, provider, scene["id"])
        carried_dismissed = {scene["id"] for scene in to_insert if scene.get("_carry_dismissed")}
        for scene in to_insert:
            if scene.pop("_carry_dismissed", False):
                release_repo.set_status(db, provider, scene["id"], entity_type, stashdb_id, "dismissed")
        if auto_handoff_callback and provider == "stashdb" and entry.get("auto_handoff"):
            for scene in to_insert:
                if scene["id"] in carried_dismissed:
                    continue  # honor an existing dismissal carried over from the other provider
                if scene["id"] in late_ids:
                    continue  # back-catalogue addition -> backlog, never auto-sent
                if _within_auto_handoff_window(scene.get("date"), window_from, window_until):
                    try:
                        auto_handoff_callback(entity_type, stashdb_id, scene["id"])
                    except Exception:
                        logger.exception(
                            "auto hand-off failed for scene %s (%s %s / %s)",
                            scene["id"], entity_type, entry["name"], stashdb_id,
                        )
    # Watermark always advances by the *full* scenes list, not just
    # to_insert -- a scene skipped as a StashDB-covered duplicate (see
    # _dedup_against_other_providers) still needs to count as "seen" for
    # this entity/provider, or it would be re-fetched and re-skipped
    # every future scan forever.
    watchlist_repo.set_watermark(db, provider, entity_type, stashdb_id, scenes[0]["created"])
    summary["new_releases"] += len(to_insert) - len(late_ids)
    summary["late_additions"] = summary.get("late_additions", 0) + len(late_ids)
    logger.info("%s %s %s: %d new release(s), %d back-catalogue addition(s) -> backlog "
                "(%d found, %d outside the date window, %d deduped against the other provider)",
                provider, entity_type, entry["name"], len(to_insert) - len(late_ids), len(late_ids),
                len(scenes), skipped, len(in_scope) - len(to_insert))


def _late_addition_days(db):
    from routers.settings import get_setting_value
    value = get_setting_value(db, "late_addition_days", "90")
    return int(value) if str(value).isdigit() else 90


def _is_late_addition(scene, days):
    """True when a scene was added to the provider more than `days`
    after its own release date (2026-09-26): the scan only "sees" it now
    because someone just entered an old scene, so it's back catalogue,
    not a new release -- it goes to the backlog (marked "New" there)
    instead of the New Releases list. 0 disables the rule. A scene
    without a release date counts as new."""
    if not days or not scene.get("date") or not scene.get("created"):
        return False
    try:
        released = datetime.strptime(scene["date"][:10], "%Y-%m-%d").date()
        created = datetime.fromisoformat(scene["created"].replace("Z", "+00:00")).date()
    except ValueError:
        return False
    return (created - released).days > days


def _within_auto_handoff_window(scene_date, from_date, until_date):
    """True if `scene_date` (the scene's own release date, "YYYY-MM-DD",
    possibly missing) falls within the entity's optional auto-handoff
    from/until bounds (same format, either or both possibly unset). A
    missing scene_date fails a bound that's actually set, rather than
    passing it by default -- can't confirm it's in range, and silently
    auto-handing off a scene with no known date defeats the point of
    the window. With no bounds set at all, always True regardless of
    scene_date."""
    if from_date and (not scene_date or scene_date < from_date):
        return False
    if until_date and (not scene_date or scene_date > until_date):
        return False
    return True


# Set by main.py: fn(db, existing_row, scene, provider, entity_type, entity_stashdb_id) -> True when it took over an IMPORTED row of another
# provider (the local scene got this provider's id, the row now lives under the scene's own id with status 'imported').
PROMOTE_HOOK = None
_UNSTARTED = ("new", "dismissed", "backlog")  # a row nothing was done with: StashDB may replace it


def _dedup_against_other_providers(db, provider, entity_type, name, entity_stashdb_id, scenes):
    """Cross-provider dedup (2026-09-11, see PROGRESS.md's "Cross-provider
    dedup" section) -- StashDB is the primary source:
    - a non-StashDB-sourced scene that StashDB (or any other already-
      watched sibling provider) already has (matched by date +
      normalized title, within the same real-world watched entity) is
      skipped entirely, never inserted;
    - a StashDB-sourced scene that a sibling provider already has is
      still inserted, but the sibling's own release row for it is
      deleted first -- StashDB "wins" and replaces it (this is also what
      makes Whisparr hand-off newly possible for that scene, since only
      a StashDB-sourced release supports hand-off, see handoff.py). An
      explicit prior dismiss is carried over so replacing the source
      doesn't silently resurrect something the user already dismissed.
    Returns the scenes that should actually be inserted.

    Generalized 2026-09-21 from a hardcoded StashDB<->ThePornDB binary
    toggle to N providers (see watchlist_repo.find_others_by_name()) --
    a real-world entity can now be watched under more than two providers
    at once (e.g. StashDB + ThePornDB + a user-added stash-box provider
    all at the same time). With more than one non-StashDB sibling and no
    StashDB among them, there's no principled way to rank two non-
    StashDB providers against each other -- whichever sibling's match is
    found first wins, same "first real match, no further tie-breaking"
    approach the original 2-provider version implicitly had (it only
    ever had one sibling to check)."""
    siblings = watchlist_repo.find_others_by_name(db, provider, entity_type, name)
    if not siblings:
        return scenes

    to_insert = []
    for scene in scenes:
        if provider == "stashdb":
            # StashDB always wins -- insert, replacing any sibling's
            # existing row for this same scene.
            keep_sibling = False
            for sibling in siblings:
                existing = release_repo.find_matching_release(
                    db, sibling["provider"], entity_type, sibling["stashdb_id"], scene.get("date"), scene.get("title"),
                )
                if existing and existing["status"] in _UNSTARTED:
                    release_repo.delete_release(
                        db, sibling["provider"], existing["scene_stashdb_id"], entity_type, sibling["stashdb_id"],
                    )
                    if existing["status"] == "dismissed":
                        scene["_carry_dismissed"] = True
                elif existing:
                    # Already on the wishlist / downloading / imported / failed (v0.130.0, found with "Anal Hooked And Fucked": the
                    # imported ThePornDB row was DELETED and the scene showed as new again). Never delete a row with progress in it:
                    # an imported one is taken over by StashDB (hook), the others stay as they are and the StashDB duplicate is not shown.
                    keep_sibling = True
                    if existing["status"] == "imported" and PROMOTE_HOOK is not None:
                        try:
                            PROMOTE_HOOK(db, existing, scene, provider, entity_type, entity_stashdb_id)
                        except Exception:
                            logger.exception("promoting the imported %s row %s to %s failed", existing["provider"],
                                             existing["scene_stashdb_id"], provider)
            if not keep_sibling:
                to_insert.append(scene)
            continue
        # Non-StashDB provider: skip this scene entirely if any sibling
        # (StashDB or otherwise) already has it.
        covered = any(
            release_repo.find_matching_release(
                db, sibling["provider"], entity_type, sibling["stashdb_id"], scene.get("date"), scene.get("title"),
            )
            for sibling in siblings
        )
        if not covered:
            to_insert.append(scene)
    return to_insert
