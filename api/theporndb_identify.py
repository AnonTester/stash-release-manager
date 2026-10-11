"""Batch-matches currently-favorited Studios/Performers against
ThePornDB, via Stash's own stash-box batch-tag mechanism (the same one
its "Tagger" UI uses) -- this is what actually populates the
`stash_ids` entries this project's ThePornDB scan/watchlist sync (see
sync.py) depends on to find anything to scan at all. Without running
this (or the equivalent manual "Identify"/"Scrape with..." in Stash's
own UI), the watchlist will never grow a `provider='theporndb'` entry
for an otherwise StashDB-only-favorited Studio/Performer.

ID-matching only, by explicit user decision (2026-09-11, see
PROGRESS.md's "ThePornDB identify sync" section): a live test showed
Stash's batch-tag mutation also overwrites other metadata (a studio's
name capitalization changed) unless those fields are excluded --
EXCLUDE_ALL_BUT_ID below covers every field ToPartial() can set, for
both studios and performers, except stash_ids itself (which can't be
excluded -- that's the one thing this is actually for).

Rate-limited in chunks (2026-09-11, same day): the batch-tag mutation
itself has no request-pacing parameter -- Stash's own job processes its
whole id list sequentially with no artificial delay between entities,
so a single call covering hundreds/thousands of favorited entities
would hit ThePornDB's stash-box endpoint as fast as each search
completes. The only documented ThePornDB stash-box rate limit found
this session (see PROGRESS.md's earlier StashDB-query-delay research)
is 240 req/min -- this module stays comfortably under that by
submitting `ids` in small batches (one Stash-side job per batch,
waited-for to completion) with an explicit delay between batches,
rather than one giant all-at-once job.
"""
import logging
import time

logger = logging.getLogger(__name__)

THEPORNDB_STASHBOX_ENDPOINT = "https://theporndb.net/graphql"

EXCLUDE_ALL_BUT_ID = [
    "name", "urls", "url", "details", "aliases", "parent", "image",
    "birthdate", "death_date", "career_start", "career_end", "career_length",
    "country", "ethnicity", "eye_color", "hair_color", "fake_tits", "gender",
    "height", "weight", "measurements", "disambiguation", "piercings",
    "tattoos", "penis_length", "circumcised", "twitter", "instagram",
]

# 20 entities/batch: even if ThePornDB's stash-box endpoint answered
# instantly (no real network/search latency at all -- the worst case for
# rate purposes), 20 requests then a >=3s pause keeps this under
# 240/min with real margin (400/min *would* be the instant-response
# worst case at these numbers; the delay is sized against that, not
# against the far slower realistic case where each search itself takes
# real time).
DEFAULT_BATCH_SIZE = 20


def _chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def identify_favorites_against_theporndb(stash_client, batch_size=DEFAULT_BATCH_SIZE,
                                          delay_seconds=3.0, poll_interval=2, timeout=120,
                                          fetch_watched=None, queue_wait_seconds=300):
    """Returns {"studios_matched": n, "performers_matched": n, "errors": [...]}
    -- "matched" means newly gained a ThePornDB stash_id this run (not
    the total favorited count). refresh=False (see
    StashClient.batch_tag_studios()/batch_tag_performers()) means an
    already-matched entity is left alone, so this is safe and cheap to
    run repeatedly on an interval -- only a *first* run against a large,
    still-mostly-unmatched watchlist is genuinely slow (one real
    ThePornDB search per entity, done by Stash's own job one at a time,
    now further paced by `delay_seconds` between each `batch_size`-sized
    chunk -- see this module's docstring for the rate-limit reasoning).
    `timeout` bounds how long *each chunk's* job is waited for before
    raising a timeout error for that chunk alone; Stash's own job isn't
    tied to this HTTP request and keeps running server-side regardless,
    so a timeout here just means that chunk's match results show up on
    a later run instead, same as the equivalent StashDB.org favorites
    sync's own long-running-call precedent (see stashdb_favorites.py).
    A single chunk's failure/timeout is logged and skipped, not fatal to
    the rest of the run -- same resilience reasoning as
    stash_client.py's _bulk_favorite(). Note:
    Stash's own batch-tag job silently skips any Studio/Performer marked
    "Organized" -- a favorited-but-Organized entity will never get
    matched this way, that's Stash's own built-in protection against
    overwriting already-curated entries, not something this project
    controls.

    `fetch_watched(entity_type)` (2026-09-26): returns the WATCHED
    entities -- favorites or the watchlist tag, see watchlist_source.py.
    Defaults to the Favorite flag (the original behavior)."""
    if fetch_watched is None:
        def fetch_watched(entity_type):
            return (stash_client.favorited_studios() if entity_type == "studio"
                    else stash_client.favorited_performers())
    before_studios = {s["name"]: s for s in fetch_watched("studio")}
    before_performers = {p["name"]: p for p in fetch_watched("performer")}

    errors = []
    postponed = False
    # Studios: only the ones without a ThePornDB id yet, with
    # refresh=True (2026-09-25, applying the workaround documented
    # 2026-09-21 -- see project_theporndb_studio_tag_crash memory /
    # PROGRESS.md "Known upstream bug"). Stash v0.31.1 panics on a
    # studio's FIRST ThePornDB match when refresh=False + exclude_fields;
    # refresh=True avoids that (confirmed live 2026-09-21). Passing only
    # still-unmatched studios keeps it as cheap as refresh=False was --
    # already-matched studios aren't re-queried at all. Became necessary
    # once ThePornDB releases get imported (v0.63.0): without the TPDB
    # id, Stash's scraper can't link an imported scene to its studio.
    studio_ids = [s["id"] for s in before_studios.values() if not s.get("theporndb_id")]
    # Performers the same way since 2026-09-25 (same day): Stash's batch
    # PERFORMER tag turned out to panic too (nil pointer, seen live in
    # jobs 92735/92739), contrary to the 2026-09-21 finding.
    performer_ids = [p["id"] for p in before_performers.values() if not p.get("theporndb_id")]

    for label, ids, run, refresh in (
        ("studio", studio_ids, stash_client.batch_tag_studios, True),
        ("performer", performer_ids, stash_client.batch_tag_performers, True),
    ):
        chunks = list(_chunked(ids, batch_size))
        for i, chunk in enumerate(chunks):
            try:
                if not stash_client.wait_queue_idle(queue_wait_seconds):
                    postponed = True  # the queue is busy (a Generate task, imports ...): do not submit behind it, try again next run
                    break
                job_id = run(chunk, THEPORNDB_STASHBOX_ENDPOINT, EXCLUDE_ALL_BUT_ID, refresh=refresh)
                stash_client.wait_for_job(job_id, poll_interval, timeout)
            except Exception as e:
                logger.warning("ThePornDB identify (%s, batch %d/%d) failed: %s",
                                label, i + 1, len(chunks), e)
                errors.append({"entity_type": label, "error": str(e)})
            if i < len(chunks) - 1:
                time.sleep(delay_seconds)
        if postponed:
            break

    after_studios = {s["name"]: s for s in fetch_watched("studio")}
    after_performers = {p["name"]: p for p in fetch_watched("performer")}

    studios_matched = sum(
        1 for name, s in after_studios.items()
        if s.get("theporndb_id") and not (before_studios.get(name) or {}).get("theporndb_id")
    )
    performers_matched = sum(
        1 for name, p in after_performers.items()
        if p.get("theporndb_id") and not (before_performers.get(name) or {}).get("theporndb_id")
    )

    summary = {"studios_matched": studios_matched, "performers_matched": performers_matched, "errors": errors}
    if postponed:
        summary["postponed"] = True
        logger.info("ThePornDB identify: Stash's job queue stayed busy for %ds -- postponed, the rest runs next time", queue_wait_seconds)
    logger.info("ThePornDB identify: %s (checked %d still-unmatched studio(s), %d still-unmatched performer(s))",
                summary, len(studio_ids), len(performer_ids))
    return summary
