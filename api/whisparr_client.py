"""Whisparr API client.

Two distinct add-movie call shapes live here on purpose -- see
the comments below for the reasoning:

- register_existing(): "Stash already has this file, just track it."
  monitored=False, searchForMovie=False. This mirrors
  the earlier script's add_movie() and is NOT what this project's
  hand-off action uses -- kept here only for completeness/potential
  reuse, the actual sync job for already-local files stays in the
  separate earlier script.
- request_download(): "This is new, go get it." monitored=True,
  searchForMovie=True. This is what the "send to Whisparr" hand-off
  action in this plugin calls.

Both auto-create Whisparr's Studio/Performer catalog entries as a side
effect of referencing their foreignId, and Whisparr's own default
`monitored` state for those auto-created parent entities could not be
verified live (see PROGRESS.md) -- so every call that might create or
touch a Studio/Performer entity is followed by force_unmonitor_entity()
to defensively pin it back to monitored:false, moviesMonitored:false,
rather than trusting Whisparr's default. safety_sweep() is the
periodic backstop for anything that slips through regardless.
"""
import time

import requests

# Cache for _find_by_foreign_id() below, module-level (not on
# WhisparrClient itself) because get_whisparr_client() in main.py
# deliberately constructs a *new* client on every call (so a
# Settings-page URL/API-key edit takes effect immediately) -- an
# instance attribute would be wiped every single call and cache
# nothing. Keyed by (base_url, resource) in case more than one
# Whisparr instance is ever configured across the process's lifetime.
_entity_cache = {}
_entity_cache_time = {}
_ENTITY_CACHE_TTL_SECONDS = 600


class WhisparrLookupEmpty(RuntimeError):
    """Whisparr's metadata knows no scene with this stash id (yet) -- its database lags behind StashDB, so a scene from the last days is
    often missing. Not an error of ours: handoff.send_to_whisparr() carries on without a Whisparr record where Whisparr is only bookkeeping."""


class MovieAlreadyExistsError(Exception):
    """Raised by _add_movie() when Whisparr's own validation reports the
    movie was already added (its `MovieExistsValidator` error, confirmed
    live 2026-09-13) -- a real scene can already exist as a Whisparr
    movie record for reasons outside this project's own hand-off flow
    (e.g. a leftover from before this plugin existed, or from Whisparr's
    historical Monitored-flag catalog import), even though this
    project's own release row never recorded a hand-off for it. Callers
    should treat this as "already tracked, look it up" rather than a
    real failure -- see handoff.py's send_to_whisparr()."""


class WhisparrClient:
    def __init__(self, url, api_key, timeout=30):
        self.base = f"{url.rstrip('/')}/api/v3"
        self.session = requests.Session()
        self.session.headers["X-Api-Key"] = api_key
        self.timeout = timeout

    def ping(self):
        """Cheapest possible authenticated call -- Whisparr/Sonarr/Radarr's
        standard system-status endpoint -- used only to verify a URL/API
        key pair actually works (see the Releases page's "Check
        connections" button). Raises on any HTTP error."""
        resp = self.session.get(f"{self.base}/system/status", timeout=self.timeout)
        resp.raise_for_status()

    def get_quality_profiles(self):
        """Backs the Settings page's quality-profile dropdowns (2026-09-12,
        see PROGRESS.md's "Per-content-type Whisparr quality profiles
        (VR)" section) -- Whisparr supports fully custom profiles (this
        instance has its own "VR" one, id 7, confirmed live), so there's
        no fixed/builtin list to hardcode here; has to be fetched live."""
        resp = self.session.get(f"{self.base}/qualityprofile", timeout=self.timeout)
        resp.raise_for_status()
        return [{"id": p["id"], "name": p["name"]} for p in resp.json()]

    def get_quality_profile_cutoff_resolution(self, profile_id):
        """Resolves a quality profile's own `cutoff` (an item id
        somewhere in its nested `items` tree, a group or a leaf) to a
        plain resolution height -- backs the "Missing from Catalog"
        completeness check (2026-09-13): a local file at or above this
        height is considered "good enough", matching Whisparr's own
        stop-upgrading behavior for a file that already meets cutoff.

        Verified live against a real profile ("HD - 720p/1080p", id 6):
        cutoff=6 resolves to the "Bluray-720p" item (resolution 720),
        even though the same profile's `items` also *allow* 1080p
        qualities above it -- the cutoff is the minimum accepted tier
        in the profile's own ordering (what Whisparr stops upgrading
        once reached), not a single fixed target resolution."""
        resp = self.session.get(f"{self.base}/qualityprofile", timeout=self.timeout)
        resp.raise_for_status()
        profile = next((p for p in resp.json() if p["id"] == profile_id), None)
        if profile is None:
            raise ValueError(f"Whisparr has no quality profile with id {profile_id}")

        def find_item(items, target_id):
            for it in items:
                if "quality" in it and it["quality"]["id"] == target_id:
                    return it["quality"]
                if it.get("id") == target_id:
                    return it
                if it.get("items"):
                    found = find_item(it["items"], target_id)
                    if found:
                        return found
            return None

        cutoff_item = find_item(profile["items"], profile["cutoff"])
        if cutoff_item is None:
            raise ValueError(f"Could not resolve cutoff item {profile['cutoff']} in profile {profile_id}")
        return cutoff_item["resolution"]

    def lookup_by_stash_id(self, stash_id):
        resp = self.session.get(
            f"{self.base}/movie/lookup", params={"term": f"stash:{stash_id}"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            raise WhisparrLookupEmpty(f"Whisparr lookup returned no metadata for stash id {stash_id}")
        return results[0]

    def rescan_movie(self, movie_id, wait_seconds=120):
        """Whisparr's own RescanMovie: re-reads the movie's folder -- a file
        that's gone is dropped, a video found in the folder is taken over IN
        PLACE (not renamed or moved; checked live 2026-09-28). Returns the
        command's final status."""
        import time
        cmd = self.session.post(f"{self.base}/command", json={"name": "RescanMovie", "movieId": movie_id},
                                timeout=self.timeout)
        cmd.raise_for_status()
        cmd_id = cmd.json()["id"]
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            status = self.session.get(f"{self.base}/command/{cmd_id}", timeout=self.timeout).json().get("status")
            if status in ("completed", "failed", "aborted"):
                return status
            time.sleep(2)
        return "timeout"

    # Shared by ALL client instances: callers build a fresh client per call
    # (get_whisparr_client()), so an instance-level cache never survived and
    # the 130k-movie list was re-downloaded for every organized file
    # (2026-09-28: ~25 s per file, 27 s in total).
    _path_cache = None
    _list_cache = None  # (time, [movies]) -- the same single GET /movie as the folder index, kept for the title lookups (stage D)

    def _load_movies(self):
        """The full movie list (133k entries, ~25 s) -- ONE request, cached class-wide for 10 minutes; also builds the folder index."""
        import time
        cache = WhisparrClient._path_cache
        if cache is None or time.time() - cache[0] > 600:
            resp = self.session.get(f"{self.base}/movie", timeout=300)
            resp.raise_for_status()
            movies = resp.json()
            by_path = {}
            for m in movies:
                by_path.setdefault((m.get("path") or "").rstrip("/"), []).append(m)
            cache = (time.time(), by_path)
            WhisparrClient._path_cache = cache
            WhisparrClient._list_cache = (cache[0], movies)
        return cache

    def movies_in_folder(self, folder):
        """Movies whose folder is exactly `folder` (the full movie list is
        large -- 130k+ -- so it's cached for 10 minutes, class-wide)."""
        return self._load_movies()[1].get(folder.rstrip("/"), [])

    def movies_by_title_year(self, title, year):
        """Whisparr entries with this title (letters/digits only, case and accents ignored) and year -- from the cached list (stage D: a duplicate of
        the same movie under another ThePornDB id)."""
        import re
        import unicodedata

        def norm(t):
            return re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()).strip()
        self._load_movies()
        wanted = norm(title)
        return [m for m in WhisparrClient._list_cache[1] if norm(m.get("title")) == wanted and (not year or m.get("year") == year)]

    def movie_cached(self, movie_id):
        """One entry of the cached list by its Whisparr id (None if unknown) -- no request when the list is fresh."""
        self._load_movies()
        return next((m for m in WhisparrClient._list_cache[1] if m.get("id") == movie_id), None)

    def set_movie_path(self, movie_id, path):
        """Points a movie at another folder WITHOUT Whisparr moving anything
        (moveFiles=false) -- the files were already moved by this sidecar."""
        movie = self.get_movie(movie_id)
        old_path = (movie.get("path") or "").rstrip("/")
        movie["path"] = path
        resp = self.session.put(f"{self.base}/movie/{movie_id}", params={"moveFiles": "false"}, json=movie,
                                timeout=self.timeout)
        resp.raise_for_status()
        cache = WhisparrClient._path_cache
        if cache is not None:  # keep the shared folder index in step
            by_path = cache[1]
            by_path[old_path] = [m for m in by_path.get(old_path, []) if m["id"] != movie_id]
            by_path.setdefault(path.rstrip("/"), []).append({**movie, "path": path})
        return resp.json()

    def find_by_stash_id(self, stash_id):
        resp = self.session.get(
            f"{self.base}/movie", params={"stashId": stash_id}, timeout=self.timeout,
        )
        resp.raise_for_status()
        results = resp.json()
        return results[0] if results else None

    def search_movie(self, movie_id):
        """Explicitly (re-)triggers Whisparr's own indexer search for an
        already-existing movie -- 2026-09-13, same mechanism Stasharr's
        own "Search" action uses, per the user's request to align with
        it. Distinct from request_download()'s `addOptions.searchForMovie`,
        which only ever runs once, as part of adding a movie for the
        first time -- this is for a movie Whisparr already knows about
        (see handoff.py's MovieAlreadyExistsError handling) where a
        fresh search might now find something the original one didn't
        (new indexer results since, etc.).

        Verified live 2026-09-13 against this real Whisparr instance's
        own command history (the user had just run one via Whisparr's
        own UI/Stasharr) -- `POST /command` with
        `{"name": "MoviesSearch", "movieIds": [id]}`. Fire-and-forget
        like the rest of this hand-off flow: Radarr-family `/command`
        endpoints queue the job and return immediately rather than
        blocking for the search's real duration (that one real run
        took ~8s end to end, confirmed after the fact via this same
        endpoint's own command history, not by waiting on this call)."""
        resp = self.session.post(
            f"{self.base}/command", json={"name": "MoviesSearch", "movieIds": [movie_id]},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def _add_movie(self, lookup_resource, quality_profile_id, root_folder_path, monitored, search_for_movie):
        payload = dict(lookup_resource)
        payload["qualityProfileId"] = quality_profile_id
        payload["rootFolderPath"] = root_folder_path
        payload["monitored"] = monitored
        payload["addOptions"] = {"searchForMovie": search_for_movie}
        resp = self.session.post(f"{self.base}/movie", json=payload, timeout=self.timeout)
        if resp.status_code == 400:
            # Whisparr's own validation, not a transport error -- confirmed
            # live 2026-09-13: a scene can already exist as a Whisparr
            # movie (e.g. from before this project's hand-off flow
            # existed, or Whisparr's historical Monitored-flag catalog
            # import) even though our own release row never recorded a
            # hand-off for it, since it got there some other way. Was
            # previously left to raise_for_status() below, surfacing as
            # an unhandled 500 all the way to the user instead of a
            # meaningful outcome.
            try:
                errors = resp.json()
            except ValueError:
                errors = None
            if isinstance(errors, list) and any(e.get("errorCode") == "MovieExistsValidator" for e in errors):
                raise MovieAlreadyExistsError(lookup_resource.get("foreignId"))
        resp.raise_for_status()
        return resp.json()

    def register_existing(self, lookup_resource, quality_profile_id, root_folder_path):
        """File already supplied locally by Stash -- bookkeeping only,
        never search. Mirrors the earlier script's add_movie(); not used
        by this plugin's own hand-off flow, kept for parity/reuse."""
        return self._add_movie(lookup_resource, quality_profile_id, root_folder_path,
                                monitored=False, search_for_movie=False)

    def request_download(self, lookup_resource, quality_profile_id, root_folder_path):
        """Genuinely new release -- tell Whisparr to search indexers and
        fetch it. This is what the "send to Whisparr" hand-off action
        calls."""
        return self._add_movie(lookup_resource, quality_profile_id, root_folder_path,
                                monitored=True, search_for_movie=True)

    def get_release(self, movie_id):
        """GET /release?movieId=X -- Whisparr's own "Interactive Search",
        confirmed live 2026-09-19 to run in ~0.5s against this
        instance's 3 configured indexers and return raw per-release
        candidates: title, guid, indexerId/indexer, protocol,
        downloadUrl, size, age, quality.quality.resolution, and
        rejected/rejections (the last two reflect WHISPARR's own
        currently-assigned quality profile, not this project's --
        indexer_search.py deliberately ignores them and applies its own
        quality_profile instead). Also confirmed live: a Prowlarr-
        proxied indexer's downloadUrl already embeds Prowlarr's own
        auth token, so this project needs no indexer credentials of its
        own to use it (see indexer_search.py/sabnzbd_client.add_url()).
        Milestone 3 (2026-09-19): this project now does its own
        selection over this raw list instead of trusting Whisparr's
        automatic MoviesSearch+grab -- see handoff.py's send_to_whisparr(),
        which now calls register_existing() instead of
        request_download()."""
        resp = self.session.get(f"{self.base}/release", params={"movieId": movie_id}, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def get_indexers(self):
        """GET /indexer -- id/name/protocol/priority for every
        configured indexer. Confirmed live 2026-09-19: `priority` is
        the same field Whisparr's own DownloadDecisionComparer.
        CompareIndexerPriority sorts releases by (lower number =
        preferred) -- indexer_search.py replicates that ordering
        without needing its own indexer credentials."""
        resp = self.session.get(f"{self.base}/indexer", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_blocklist(self):
        """Every blocklisted release across ALL movies -- `GET
        /blocklist`, confirmed live 2026-09-23 this does NOT actually
        filter by a `movieId` query param (a real API quirk -- ignores
        it silently, `totalRecords` stays the same with or without it;
        the properly-filtered per-movie endpoint is `GET
        /blocklist/movie?movieId=X` instead, not used here since this
        project wants the whole list). `pageSize=5000` is a single-call
        "comfortably more than this will realistically ever hold"
        request rather than real pagination, same convention already
        used for get_queue()'s own pageSize=250 -- see
        whisparr_blocklist_sync.py, which reads each entry's own
        `movieId`/`sourceTitle`."""
        resp = self.session.get(
            f"{self.base}/blocklist", params={"page": 1, "pageSize": 5000}, timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json().get("records", [])

    def set_movie_monitored(self, movie_id, monitored):
        """GET-modify-PUT the movie's own `monitored` flag. Added
        2026-09-19 for the manual "hand off to Whisparr" fallback (see
        handoff.py's fallback_to_whisparr()) -- flips a
        register_existing()-added movie (monitored=False, since this
        project now owns search/grab by default) back to True before
        triggering Whisparr's own automatic search, for the case where
        this project's own indexer_search hasn't found anything
        acceptable yet."""
        resp = self.session.get(f"{self.base}/movie/{movie_id}", timeout=self.timeout)
        resp.raise_for_status()
        movie = resp.json()
        movie["monitored"] = monitored
        put_resp = self.session.put(f"{self.base}/movie/{movie_id}", json=movie, timeout=self.timeout)
        put_resp.raise_for_status()
        return put_resp.json()

    def find_grabbed_history_event(self, movie_id):
        """Most recent 'grabbed' event in this movie's history, or None
        -- 2026-09-14, added for the "fix mismatched download" flow (see
        delete_movie()/handoff.py's fix_mismatched_download()). Confirmed
        live this can legitimately be None: a movie whose file arrived
        via a disk scan / pre-existing import (Whisparr's own
        `diskScanImported`/`downloadFolderImported` events, no
        `grabbed` before them) was never the result of a real Whisparr
        search+grab in the first place, so there's nothing here for
        history/failed/{id} to blocklist -- best-effort, callers must
        handle None."""
        resp = self.session.get(
            f"{self.base}/history/movie", params={"movieId": movie_id}, timeout=self.timeout,
        )
        resp.raise_for_status()
        grabbed = [e for e in resp.json() if e.get("eventType") == "grabbed"]
        if not grabbed:
            return None
        return max(grabbed, key=lambda e: e["date"])["id"]

    def find_grabbed_download_id(self, movie_id):
        """Most recent 'grabbed' event's `downloadId` for this movie, or
        None -- added 2026-09-19 for download_sources.SabnzbdSource,
        which needs to match a release back to a specific SABnzbd job.
        `downloadId` is the download client's own job id (SABnzbd's
        `nzo_id`), confirmed live to survive even once the job has
        dropped out of Whisparr's own `/queue` entirely (see
        PROGRESS.md's Milestone 2 cutover investigation, 2026-09-19) --
        unlike `/queue`, `/history` keeps the original grab record
        indefinitely."""
        resp = self.session.get(
            f"{self.base}/history/movie", params={"movieId": movie_id}, timeout=self.timeout,
        )
        resp.raise_for_status()
        grabbed = [e for e in resp.json() if e.get("eventType") == "grabbed"]
        if not grabbed:
            return None
        return max(grabbed, key=lambda e: e["date"]).get("downloadId")

    def mark_history_failed(self, history_id):
        """POST /history/failed/{id} -- Whisparr's own "Mark as Failed"
        action (confirmed via its OpenAPI schema 2026-09-14, matching
        the user's own recollection of the UI's queue-delete blocklist
        checkbox): blocklists the release tied to this specific grab
        event so a future search won't grab the exact same
        release/indexer combo again."""
        resp = self.session.post(f"{self.base}/history/failed/{history_id}", timeout=self.timeout)
        resp.raise_for_status()

    def get_queue(self):
        """Every currently-tracked download, including its own
        `outputPath` (location on the shared volume) and
        `trackedDownloadState` -- confirmed live 2026-09-17 via
        Whisparr's OpenAPI schema (`QueueResource`) this is exactly
        what its own UI's queue page shows. Milestone 2's
        download_sources.WhisparrQueueSource filters this for
        `trackedDownloadState == "importPending"` -- a completed
        download Whisparr itself is NOT going to auto-import, once
        `enableCompletedDownloadHandling` is turned off (see
        PROGRESS.md's "File Handling" section) -- to import ourselves.
        `pageSize=250` is just "comfortably more than this queue will
        realistically ever hold at once", not a real pagination need."""
        resp = self.session.get(
            f"{self.base}/queue", params={"pageSize": 250}, timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["records"]

    def history_since(self, date_iso):
        """GET /history/since -- every history event (all movies) since `date_iso` (UTC, 'YYYY-MM-DDTHH:MM:SSZ') in ONE request: grabs, imports,
        failures. Added for the movies (stage C): the sidecar learns what Whisparr did without asking per movie."""
        resp = self.session.get(f"{self.base}/history/since", params={"date": date_iso}, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_movie(self, movie_id):
        """GET /movie/{id} -- used by download_sources.py to resolve a
        queue item's `movieId` back to the `stashId` this project's own
        `release` rows are keyed by."""
        resp = self.session.get(f"{self.base}/movie/{movie_id}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def clear_queue_item(self, queue_id):
        """DELETE /queue/{id} once file_importer.py has successfully
        imported a completed download itself -- `removeFromClient=False`
        deliberately (only clear Whisparr's own tracking of the
        download; the actual downloaded file has already been moved out
        by us at this point, no reason to also reach into the download
        client's own history/files). `blocklist=False` -- a successful
        import is not a bad release."""
        resp = self.session.delete(
            f"{self.base}/queue/{queue_id}",
            params={"removeFromClient": "false", "blocklist": "false"},
            timeout=self.timeout,
        )
        resp.raise_for_status()

    def get_completed_download_handling(self):
        """Whisparr's own Settings > Media Management > "Completed
        Download Handling" toggle, read back live -- backs the Settings
        page's file-import-source switch (2026-09-19, see
        routers/file_import.py) so the UI can show whether the two
        systems currently agree instead of trusting whatever this
        project last set."""
        resp = self.session.get(f"{self.base}/config/downloadclient/1", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()["enableCompletedDownloadHandling"]

    def set_completed_download_handling(self, enabled):
        """Flips Whisparr's own Completed Download Handling toggle --
        must be OFF for this sidecar's own file-import pipeline
        (SabnzbdSource) to have any completed download to find before
        Whisparr imports it itself, and back ON when switching back to
        trusting Whisparr's own built-in import. Added 2026-09-19 after
        this was previously only ever done by hand via a raw API call
        (see PROGRESS.md's Milestone 2 cutover investigation) -- now a
        real switch on the Settings page instead of a manual step
        that's easy to forget or leave out of sync."""
        config = self.session.get(f"{self.base}/config/downloadclient/1", timeout=self.timeout)
        config.raise_for_status()
        body = config.json()
        body["enableCompletedDownloadHandling"] = enabled
        resp = self.session.put(f"{self.base}/config/downloadclient/1", json=body, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()["enableCompletedDownloadHandling"]

    def delete_movie_file(self, file_id):
        """DELETE /moviefile/{id}: drops ONLY Whisparr's record of the file (the movie stays, hasFile becomes false). For a record whose file
        is gone from disk; it never touches a file."""
        resp = self.session.delete(f"{self.base}/moviefile/{file_id}", timeout=self.timeout)
        resp.raise_for_status()

    def delete_movie(self, movie_id, delete_files=True, add_import_exclusion=False):
        """DELETE /movie/{id}?deleteFiles=...&addImportExclusion=... --
        confirmed via Whisparr's OpenAPI schema 2026-09-14 (the
        DELETE /moviefile/{id} endpoint has no such option, it only
        drops Whisparr's own tracking record and leaves the physical
        file on disk -- this is the one that actually removes it, same
        as the "delete file too?" prompt in Whisparr's own UI). Removes
        the *entire* movie, not just its file -- the "fix mismatched
        download" flow re-adds it fresh right after via
        request_download(), which also resets it to a normal "wanted"
        search instead of the upgrade-only search a re-triggered
        MoviesSearch would run against a movie that still has
        (the wrong) file."""
        resp = self.session.delete(
            f"{self.base}/movie/{movie_id}",
            params={"deleteFiles": delete_files, "addImportExclusion": add_import_exclusion},
            timeout=self.timeout,
        )
        resp.raise_for_status()

    # ---- Studio/Performer monitored-flag guard ---------------------------
    #
    # Per-item GET-modify-PUT, NOT the bulk `/{resource}/editor` endpoint.
    # Found live that PUT /api/v3/studio/editor can return
    # 202 while silently failing to persist `moviesMonitored: false` --
    # 48 studios sat at `moviesMonitored: true` for a full day after a
    # bulk-editor call had reported success, and a later manually-
    # triggered RefreshStudios added 1,932 new movies for one of them
    # before it was caught. The per-item PUT (GET the full object, flip
    # both fields, PUT the whole object back) was verified to persist
    # correctly where the bulk editor didn't. Since this project's entire
    # "defensive guard" premise is trusting that these calls actually
    # clear the flags, silently keeping the unreliable bulk endpoint here
    # would undermine the one thing this code exists to guarantee -- see
    # PROGRESS.md's "Whisparr bulk /editor endpoint is unreliable"
    # section for the full incident this was ported from.

    def force_unmonitor_studio(self, studio_ids):
        return self._unmonitor_each("studio", studio_ids)

    def force_unmonitor_performer(self, performer_ids):
        return self._unmonitor_each("performer", performer_ids)

    def _unmonitor_each(self, resource, ids):
        """Returns {"succeeded": [ids], "failed": [ids]} -- "failed"
        means the post-write GET still showed monitored or
        moviesMonitored true (or the call itself errored); a caller that
        cares should treat a nonempty "failed" list as still dangerous
        and retry/alert, not assume the PUT response alone means it
        worked."""
        succeeded, failed = [], []
        for entity_id in ids:
            try:
                resp = self.session.get(f"{self.base}/{resource}/{entity_id}", timeout=self.timeout)
                resp.raise_for_status()
                obj = resp.json()
                obj["monitored"] = False
                obj["moviesMonitored"] = False
                put_resp = self.session.put(
                    f"{self.base}/{resource}/{entity_id}", json=obj, timeout=self.timeout,
                )
                put_resp.raise_for_status()

                verify_resp = self.session.get(f"{self.base}/{resource}/{entity_id}", timeout=self.timeout)
                verify_resp.raise_for_status()
                verified = verify_resp.json()
                if verified.get("monitored") or verified.get("moviesMonitored"):
                    failed.append(entity_id)
                else:
                    succeeded.append(entity_id)
            except requests.RequestException:
                failed.append(entity_id)
        return {"succeeded": succeeded, "failed": failed}

    def find_studio_by_foreign_id(self, foreign_id):
        return self._find_by_foreign_id("studio", foreign_id)

    def find_performer_by_foreign_id(self, foreign_id):
        return self._find_by_foreign_id("performer", foreign_id)

    def _find_by_foreign_id(self, resource, foreign_id):
        """Whisparr's Studio/Performer endpoints have no server-side
        filter by foreignId (unlike /movie?stashId=, confirmed live
        2026-09-13 -- a ?foreignId= query param is silently ignored,
        same response either way), so resolving one foreignId to its
        Whisparr numeric id means fetching the *entire* Studio or
        Performer list and filtering client-side. Against this real
        instance that's 940 studios / ~14s, or 16809 performers -- both
        comfortably under the 30s default timeout on a good day, but a
        real hand-off (user report 2026-09-13: "Hand-off failed: HTTP
        500" on multiple Some Performer releases) hit exactly this call
        timing out at 30s, presumably some slower moment for Whisparr's
        own DB. This is the same "never fetch the whole catalog"
        problem this project avoids everywhere else for /movie --
        _reset_parent_entities() calls this on *every single hand-off*
        for the same small, repeating set of favorited studios/
        performers, so an in-process cache (module-level, see top of
        file) turns "fetch 940 studios on every hand-off" into "fetch
        once per TTL window (or once per newly-encountered studio)."
        A cache miss still does the full fetch -- this doesn't remove
        the underlying slowness, just how often it's paid."""
        cache_key = (self.base, resource)
        cached = _entity_cache.get(cache_key)
        age = time.monotonic() - _entity_cache_time.get(cache_key, 0)
        if cached is None or foreign_id not in cached or age > _ENTITY_CACHE_TTL_SECONDS:
            resp = self.session.get(f"{self.base}/{resource}", timeout=self.timeout)
            resp.raise_for_status()
            cached = {item["foreignId"]: item for item in resp.json() if item.get("foreignId")}
            _entity_cache[cache_key] = cached
            _entity_cache_time[cache_key] = time.monotonic()
        return cached.get(foreign_id)

    def safety_sweep(self):
        """Scan every Studio and Performer currently in Whisparr and
        force-reset any with monitored:true OR moviesMonitored:true back
        to false (checking `moviesMonitored` alone matters -- the
        incident above showed it can be true and actively dangerous
        even while `monitored` is already false). Periodic backstop
        against the full-catalog-sync bug re-triggering via a side
        channel we can't fully control (Whisparr's own auto-create
        default for parent entities referenced by a newly added movie)
        -- see PROGRESS.md's 2026-09-11 "Defensive guard" section.
        Returns {"studio": {"succeeded": [...], "failed": [...]},
        "performer": {...}}."""
        reset = {"studio": {"succeeded": [], "failed": []}, "performer": {"succeeded": [], "failed": []}}
        for resource in ("studio", "performer"):
            resp = self.session.get(f"{self.base}/{resource}", timeout=self.timeout)
            resp.raise_for_status()
            offenders = [item["id"] for item in resp.json()
                         if item.get("monitored") or item.get("moviesMonitored")]
            if offenders:
                reset[resource] = self._unmonitor_each(resource, offenders)
        return reset
