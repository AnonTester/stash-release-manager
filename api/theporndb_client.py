"""ThePornDB REST API client.

Confirmed live 2026-09-11: ThePornDB has *two* separate APIs, easy to
conflate --

- A stash-box-*shaped* GraphQL endpoint at `https://theporndb.net/graphql`
  (what local Stash itself talks to for scraping/identification --
  configured there under Settings > Metadata Providers > Stash-box
  Endpoints, confirmed live). It accepts the same ApiKey-header auth and
  queryStudios/queryPerformers shape as StashDB, so it's useful for
  resolving a local Studio/Performer's ThePornDB id (same mechanism as
  `stash_client.py`'s existing StashDB `stash_ids` lookup) -- BUT its
  `queryScenes` is broken: every query tried returns a non-zero `count`
  with `scenes: null`, reproduced with and without filters. So it's not
  usable for the actual "what's new" scan.
- The real, working REST API at `https://api.theporndb.net` (Bearer
  token from https://theporndb.net/user/api-tokens -- the same token
  used as the "API key" on Stash's Stash-box Endpoint config above).
  `GET /scenes?performer_id=<numeric>` / `?site_id=<numeric>` returns
  scenes newest-`created`-first by default (verified live), each scene
  carrying both `id` (a UUID -- the *same* id space as the GraphQL
  endpoint above) and `_id`/`site.id` (a numeric id -- a *different* id
  space, and the one this REST API's own filters need). A local
  Studio/Performer's `stash_ids` entry only ever gives the UUID, so
  `resolve_native_id()` below does one extra REST call
  (`GET /performers/{uuid}` or `/sites/{uuid}`) to translate it to the
  numeric id this class's other methods need -- done once at watchlist
  sync time (see sync.py), not on every scan.

This class only implements what the scan needs: resolving a native id,
and fetching new/newest scenes for one already-resolved entity. No
watchlist-favorites concept here (that's a StashDB.org-account-specific
feature, doesn't apply to ThePornDB).
"""
import time

import requests

from http_deadline import call_with_deadline
from stashdb_client import FeedIncomplete

# entity_type -> (REST path segment for id resolution, numeric-id field
# name in that resource's JSON, scene-list query param). Two real
# inconsistencies in ThePornDB's own API confirmed live: performers
# carry their numeric id under `_id` while sites carry theirs under
# plain `id` (with the UUID under `uuid` instead) -- not a bug in this
# client, just how their API actually responds.
_ENTITY = {
    "performer": {"resolve_path": "performers", "numeric_field": "_id", "scene_param": "performer_id"},
    "studio": {"resolve_path": "sites", "numeric_field": "id", "scene_param": "site_id"},
}


def best_image(scene):
    """The scene image to show as a thumbnail (v0.93.1). `image` is the ORIGINAL URL on the source site's own CDN -- hotlinked, so often
    blocked (403) or gone (404) -- while ThePornDB mirrors its own copies: `background` (landscape, small = 500x282, ~15 KB) and `poster`
    (portrait 800x1200). Mirrored landscape first, the source URL only as the last resort."""
    bg = scene.get("background") or {}
    for url in (bg.get("small"), bg.get("medium"), scene.get("poster"), scene.get("image")):
        if isinstance(url, str) and url.startswith("http"):
            return url
    return None


class ThePornDBClient:
    def __init__(self, url, api_key, timeout=15, delay=0.3):
        self.base = url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_key}"
        self.session.headers["Accept"] = "application/json"
        self.timeout = timeout
        self.delay = delay

    def _get(self, path, params=None):
        """All ThePornDB requests go through here, wrapped with a hard
        wall-clock deadline (self.timeout + a buffer) independent of
        `requests`' own timeout= -- see http_deadline.py's docstring for
        why that alone isn't a reliable cap (confirmed live 2026-09-12
        as the likely cause of a scan thread hanging indefinitely, see
        PROGRESS.md's "Scan appeared to hang permanently" section)."""
        return call_with_deadline(
            self.session.get, f"{self.base}{path}", params=params, timeout=self.timeout,
            deadline=self.timeout + 15,
        )

    def ping(self):
        """Cheapest available authenticated call -- there's no dedicated
        health/whoami endpoint, so this is a 1-result site listing, same
        idea as the other clients' ping()."""
        resp = self._get("/sites", params={"page": 1})
        resp.raise_for_status()

    def resolve_native_id(self, entity_type, uuid):
        """Translates a local Stash `stash_ids` UUID (ThePornDB's
        stash-box-shim id space) into the numeric id ThePornDB's own
        REST API needs for scene filtering. Raises on any HTTP error or
        an unexpectedly-shaped response -- the caller (sync.py) is
        expected to catch this per-entity and skip, not abort the whole
        sync, same pattern as watcher.py's per-entity scan error
        handling."""
        cfg = _ENTITY[entity_type]
        resp = self._get(f"/{cfg['resolve_path']}/{uuid}")
        resp.raise_for_status()
        data = resp.json()["data"]
        return str(data[cfg["numeric_field"]])

    def fetch_newest_scene_created(self, entity_type, native_id):
        """Single request, page 1 / per_page 1 -- mirrors
        stashdb_client.py's method of the same name, same first-run
        baseline reasoning."""
        scenes = self._fetch_scenes_page(entity_type, native_id, page=1, per_page=1)
        return scenes[0]["created"] if scenes else None

    def fetch_new_scenes(self, entity_type, native_id, since_created):
        """Yields normalized scene dicts (same shape as
        stashdb_client.py's fetch_new_scenes -- `id`, `title`, `date`,
        `created`, `studio.name`) newer than since_created, newest
        first. Relies on ThePornDB's own default scene ordering being
        newest-`created`-first (confirmed live for both performer_id and
        site_id filters 2026-09-11), same stop-early-at-the-watermark
        logic as the StashDB client."""
        page = 1
        while True:
            scenes = self._fetch_scenes_page(entity_type, native_id, page, per_page=25)
            if not scenes:
                return
            for s in scenes:
                if since_created is not None and s["created"] <= since_created:
                    return
                yield s
            page += 1
            time.sleep(self.delay)

    def fetch_recent_scenes(self, since_created, per_page=100, max_pages=100):
        """Global "recently created" feed (2026-09-25, see
        stashdb_client.fetch_recent_scenes() for the idea). `orderBy=
        recently_created` confirmed live to be strictly created-desc,
        also across pages. ThePornDB caps a listing at 10000 results (100
        pages x 100) -- only ~27h of its ~8900 new scenes/day -- so
        running out before reaching `since_created` raises FeedIncomplete
        and the caller falls back to per-entry scanning. `_studio_ids`/
        `_performer_ids` hold the NUMERIC ids a watchlist entry's
        `native_id` uses (site id; the canonical performer's `_id`, i.e.
        the parent profile's when the scene lists a site-specific one),
        as strings."""
        page = 1
        while page <= max_pages:
            resp = self._get("/scenes", params={"orderBy": "recently_created", "page": page, "per_page": per_page})
            resp.raise_for_status()
            raw = resp.json().get("data") or []
            if not raw:
                break
            for s in raw:
                if s["created"] <= since_created:
                    return
                site = s.get("site") or {}
                performer_ids = set()
                for p in s.get("performers") or []:
                    canonical = (p.get("parent") or {}).get("_id") or p.get("_id")
                    if canonical is not None:
                        performer_ids.add(str(canonical))
                yield {
                    "id": s["id"],
                    "title": s.get("title") or "",
                    "date": s.get("date"),
                    "created": s["created"],
                    "studio": {"id": site.get("uuid"), "name": site.get("name") or ""},
                    "image_url": best_image(s),
                    "details": s.get("description") or "",
                    "tags": [{"name": t.get("name")} for t in (s.get("tags") or []) if t.get("name")],
                    "is_vr": "vr" in (site.get("name") or "").lower(),
                    "_studio_ids": {str(s["site_id"])} if s.get("site_id") is not None else set(),
                    "_performer_ids": performer_ids,
                }
            page += 1
            time.sleep(self.delay)
        raise FeedIncomplete(f"feed didn't reach {since_created} within {page - 1} page(s)")

    def fetch_newest_movie_created(self):
        """ONE request: the `created` of the newest movie in the global feed -- the first-run baseline of the movie feed."""
        resp = self._get("/movies", params={"orderBy": "recently_created", "page": 1, "per_page": 1})
        resp.raise_for_status()
        raw = resp.json().get("data") or []
        return raw[0]["created"] if raw else None

    def fetch_recent_movies(self, since_created, per_page=100, max_pages=20):
        """Global "recently created" MOVIE feed (Phase 3, film_feed.py): the raw /movies records newer than `since_created`, newest first
        (`orderBy=recently_created`, confirmed live 2026-10-06; `site_id` is the same numeric studio id the watchlist keeps as
        `native_id`). Normally ONE request per run. Running out of pages before the watermark raises FeedIncomplete."""
        page = 1
        while page <= max_pages:
            resp = self._get("/movies", params={"orderBy": "recently_created", "page": page, "per_page": per_page})
            resp.raise_for_status()
            raw = resp.json().get("data") or []
            if not raw:
                return
            for m in raw:
                if m["created"] <= since_created:
                    return
                yield m
            if len(raw) < per_page:
                return
            page += 1
            time.sleep(self.delay)
        raise FeedIncomplete(f"movie feed didn't reach {since_created} within {page - 1} page(s)")

    def _fetch_scenes_page(self, entity_type, native_id, page, per_page):
        param = _ENTITY[entity_type]["scene_param"]
        resp = self._get("/scenes", params={param: native_id, "page": page, "per_page": per_page})
        resp.raise_for_status()
        raw = resp.json().get("data") or []
        return [
            {
                "id": s["id"],
                "title": s.get("title") or "",
                "date": s.get("date"),
                "created": s["created"],
                "studio": {"id": (s.get("site") or {}).get("uuid"), "name": (s.get("site") or {}).get("name") or ""},
                "tags": [{"name": t.get("name")} for t in (s.get("tags") or []) if t.get("name")],
                # A single top-level field here already, unlike StashDB's
                # `images` array shape (see stashdb_client.py's
                # fetch_new_scenes() flattening) -- kept under the same
                # `image_url` key so insert_release() doesn't need to
                # care which provider a scene came from.
                "image_url": best_image(s),
                "details": s.get("description") or "",
                # No structured VR indicator exists on ThePornDB's scene
                # or site objects (checked live 2026-09-12 against real
                # VR-titled scenes: `format` was unpopulated, no VR tag,
                # no site-level flag) -- studio/site name is the only
                # signal available, same limitation StashDB doesn't have
                # (it has a real "Virtual Reality" tag, see
                # stashdb_client.py's VR_TAG_NAME).
                "is_vr": "vr" in ((s.get("site") or {}).get("name") or "").lower(),
            }
            for s in raw
        ]

    def fetch_activity(self, entity_type, uuid, native_id):
        """Same shape as StashDBClient.fetch_activity()'s values, for ONE
        entity (2026-09-26, inactive_check.py -- only asked for the few
        entities StashDB already suggests are inactive, as a second
        opinion: ThePornDB often has newer scenes). `/scenes` for an
        entity comes newest-release-first by default (checked live
        2026-09-26, `orderBy=recently_released` gives the same order).
        Career/death only exist on performers; ThePornDB's
        career_end_year is unreliable on its own (seen live: 2012 for a
        performer with scenes in 2026), hence only informational there."""
        param = _ENTITY[entity_type]["scene_param"]
        resp = self._get("/scenes", params={param: native_id, "orderBy": "recently_released",
                                            "page": 1, "per_page": 1})
        resp.raise_for_status()
        body = resp.json()
        scenes = body.get("data") or []
        result = {
            "newest_scene_date": scenes[0].get("date") if scenes else None,
            "scene_count": (body.get("meta") or {}).get("total"),
            "career_end_year": None, "death_date": None, "deleted": False,
        }
        if entity_type == "performer":
            time.sleep(self.delay)
            detail = self._get(f"/performers/{uuid}")
            if detail.status_code == 404:
                result["deleted"] = True
            else:
                detail.raise_for_status()
                extras = (detail.json().get("data") or {}).get("extras") or {}
                result["career_end_year"] = extras.get("career_end_year")
                result["death_date"] = extras.get("deathday")
        return result

    def get_scene_detail(self, scene_id):
        """Title/date/studio/performers(with gender) for one scene, in
        EXACTLY the shape stashdb_client.get_scene_detail() returns
        ({"title", "date", "studio": {"name"}, "performers":
        [{"performer": {"name", "gender"}}]}) -- so file_naming.py and the
        Whisparr-free hand-off (2026-09-25, handoff.hand_off()) work for
        both providers unchanged. `studio` is TPDB's `site`. Gender comes
        from the performer's own `extra` or its parent profile's
        `extras`, upper-cased like StashDB's ("FEMALE"); often empty for
        brand-new performers. None if TPDB doesn't know the scene."""
        resp = self._get(f"/scenes/{scene_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        s = resp.json().get("data") or {}
        if not s:
            return None
        performers = []
        for p in s.get("performers") or []:
            gender = ((p.get("extra") or {}).get("gender")
                      or ((p.get("parent") or {}).get("extras") or {}).get("gender"))
            name = p.get("name") or (p.get("parent") or {}).get("name")
            if name:
                performers.append({"performer": {"name": name, "gender": gender.upper() if gender else None}})
        return {
            "id": s.get("id"), "title": s.get("title"), "date": s.get("date"),
            "details": s.get("description") or "",
            "image_url": best_image(s),
            "tags": [{"name": t.get("name")} for t in (s.get("tags") or []) if t.get("name")],
            # `id` = the site UUID -- the id Stash stores for a ThePornDB
            # studio (2026-09-28, file_importer's local studio lookup).
            "studio": ({"id": (s.get("site") or {}).get("uuid"), "name": (s.get("site") or {}).get("name")}
                       if s.get("site") else None),
            "performers": performers,
        }

    def find_scenes_by_hash(self, hash_value, hash_type):
        """Reverse lookup (2026-09-29, folder import): the scene(s) registered with this
        file hash -- `hash_type` OSHASH / PHASH / MD5. Returns [{"id" (the UUID Stash
        stores), "title"}], [] when nothing matches."""
        if not hash_value:
            return []
        resp = self._get("/scenes", params={"hash": hash_value, "hashType": hash_type})
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return [{"id": s.get("id"), "title": s.get("title")} for s in (resp.json().get("data") or []) if s.get("id")]

    def get_scene_fingerprints(self, scene_id):
        """Crowd-sourced file hashes for one scene, in stashdb_client.
        get_scene_fingerprints()'s shape ([{"hash", "algorithm"}],
        algorithm upper-case e.g. "PHASH"/"OSHASH") -- TPDB's `hashes`
        field (confirmed live 2026-09-25: populated for older scenes,
        usually EMPTY for brand-new ones, see verification.py's
        "unverified" handling). Empty list if none/unknown scene."""
        resp = self._get(f"/scenes/{scene_id}")
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        s = resp.json().get("data") or {}
        return [{"hash": h.get("hash"), "algorithm": (h.get("type") or "").upper()}
                for h in s.get("hashes") or [] if h.get("hash") and h.get("type")]

    def fetch_scene_image(self, scene_id):
        """One-off per-scene lookup, not part of the regular scan path --
        used only by the image_url backfill for releases discovered
        before that column existed (see db.py's
        `_migrate_release_image_url()`)."""
        resp = self._get(f"/scenes/{scene_id}")
        resp.raise_for_status()
        data = resp.json()
        scene = data.get("data", data)
        return scene.get("image") if scene else None

    def search_performers(self, term, limit=100):
        """Free-text performer search (2026-09-19, backs the plugin's
        own Search page -- see PROGRESS.md/roadmap memory). `?q=` is
        ThePornDB's own REST search param, confirmed live 2026-09-19;
        `per_page` (confirmed live it respects up to at least 100,
        despite not being documented) is used directly instead of
        over-fetching page 1 and truncating client-side.

        Returns `{"total", "results"}`, not a plain list -- confirmed
        live 2026-09-19 that a broad term's own result ordering isn't
        reliably relevance-ranked here either (same issue found on
        StashDB, see stashdb_client.py's search_performers() docstring
        for the concrete example), so silently truncating without
        telling the caller how many more exist would recreate the same
        "did the search break, or did I just not scroll far enough"
        confusion. `meta.total` is ThePornDB's own reported match count."""
        resp = self._get("/performers", params={"q": term, "per_page": limit})
        resp.raise_for_status()
        body = resp.json()
        results = [{"id": p["id"], "name": p["name"], "image_url": p.get("image"), "slug": p.get("slug")} for p in (body.get("data") or [])]
        return {"total": (body.get("meta") or {}).get("total"), "results": results}

    def search_studios(self, term, limit=100):
        """`/sites` is ThePornDB's own name for what this project calls
        a studio elsewhere (see the module docstring's `_ENTITY` map).
        Uses the site's `uuid` (not its numeric `id`) as the normalized
        id -- the same id space `resolve_native_id()` expects and the
        one a local Stash `stash_ids` entry actually carries. Returns
        `{"total", "results"}` -- see search_performers()'s docstring."""
        resp = self._get("/sites", params={"q": term, "per_page": limit})
        resp.raise_for_status()
        body = resp.json()
        results = [
            {"id": s["uuid"], "name": s["name"], "image_url": s.get("poster") or s.get("logo")}
            for s in (body.get("data") or [])
        ]
        return {"total": (body.get("meta") or {}).get("total"), "results": results}

    def search_scenes(self, term, limit=100):
        """Same normalized shape as fetch_new_scenes() (so a result can
        go straight into release_repo.insert_release() unmodified, see
        routers/search.py) plus `performers` and a real `studio.id`
        (the site's `uuid`) -- fetch_new_scenes() doesn't need either
        since it's always called for one already-known entity, but a
        search result needs both to let the user pick which entity a
        hand-off should be recorded against. Returns `{"total",
        "results"}` -- see search_performers()'s docstring."""
        resp = self._get("/scenes", params={"q": term, "per_page": limit})
        resp.raise_for_status()
        body = resp.json()
        results = []
        for s in (body.get("data") or []):
            site = s.get("site") or {}
            results.append({
                "id": s["id"], "title": s.get("title") or "", "date": s.get("date"), "created": s["created"],
                "studio": {"id": site.get("uuid"), "name": site.get("name") or ""},
                "performers": [{"id": p["id"], "name": p["name"]} for p in (s.get("performers") or [])],
                "image_url": best_image(s), "slug": s.get("slug"),
                "details": s.get("description") or "",
                "duration": s.get("duration"),
                "is_vr": "vr" in (site.get("name") or "").lower(),
            })
        return {"total": (body.get("meta") or {}).get("total"), "results": results}
