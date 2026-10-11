"""SABnzbd API client.

Added 2026-09-19 while investigating the Milestone 2 live cutover's
stuck "Show - Scene 3" hand-off (see PROGRESS.md and the in-progress
memory note) -- Whisparr's own `/queue` dropped the completed download
without ever exposing it as `importPending`, and this project had no
way to ask SABnzbd itself (the actual download client) what happened
to the job. Also the connection Milestone 3 will eventually need for
its own `SabnzbdSource` (see download_sources.py's module docstring),
so this is real infra, not a one-off diagnostic script.

SABnzbd's API is a single endpoint (`/sabnzbd/api`) dispatched by a
`mode` query param, JSON in and out -- no REST resource shape like
Whisparr/Stash. See
https://sabnzbd.org/wiki/advanced/api for the mode reference.
"""
import requests


class SabnzbdClient:
    def __init__(self, url, api_key, timeout=30):
        self.base = f"{url.rstrip('/')}/api"
        self.api_key = api_key
        self.timeout = timeout

    def _call(self, mode, **params):
        # Re-raised WITHOUT the request URL (2026-09-25): the API key is a
        # query param here, and requests' own exception text includes the
        # full URL -- it would otherwise leak into logs and the Settings
        # "Check" status. Same fix as newznab_client.py.
        try:
            resp = requests.get(self.base, params={
                "mode": mode, "output": "json", "apikey": self.api_key, **params,
            }, timeout=self.timeout)
        except requests.RequestException as e:
            raise RuntimeError(f"{type(e).__name__} contacting {self.base}") from None
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code} from {self.base}")
        data = resp.json()
        # SABnzbd reports an auth/parameter failure as HTTP 200 with an
        # `error` key (confirmed live 2026-09-19 -- e.g. "API Key
        # Incorrect") rather than a non-2xx status, so callers can't
        # rely on raise_for_status() alone to catch a bad key.
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(data["error"])
        return data

    def ping(self):
        """`mode=queue` needs a valid API key (unlike `mode=version`,
        which is unauthenticated and so useless as a key check) --
        cheapest authenticated call available, same role as the other
        clients' ping()."""
        self._call("queue", start=0, limit=1)

    def get_queue(self):
        """Every job SABnzbd currently has queued/downloading. Returns
        the raw list of slot dicts from `queue.slots` -- shape kept
        as-is (not normalized to this project's own dict shape like
        download_sources.py does for Whisparr) since nothing downstream
        consumes this yet; this is diagnostic-only for now."""
        return self._call("queue", start=0, limit=100).get("queue", {}).get("slots", [])

    def get_history(self, search=None, limit=50):
        """Completed/failed jobs. `storage` on each entry is the actual
        final path SABnzbd wrote the completed job to -- the field that
        answers "where did this file actually end up", which was the
        whole point of adding this client. `search` filters
        server-side by job name substring (SABnzbd's own `search`
        param), case-insensitive."""
        params = {"start": 0, "limit": limit}
        if search:
            params["search"] = search
        return self._call("history", **params).get("history", {}).get("slots", [])

    def add_url(self, url, category=None, nzbname=None):
        """`mode=addurl` -- SABnzbd fetches the URL itself server-side.
        Added 2026-09-19 for Milestone 3: the sidecar now owns indexer
        search/selection (see indexer_search.py), and a chosen release's
        `downloadUrl` (from Whisparr's own `GET /release`) is confirmed
        live to already be self-contained/authenticated for its
        Prowlarr-proxied indexers -- no need to download and re-upload
        the .nzb bytes ourselves. Returns the list of `nzo_ids` SABnzbd
        assigned (normally exactly one)."""
        params = {"name": url}
        if category:
            params["cat"] = category
        if nzbname:
            params["nzbname"] = nzbname
        result = self._call("addurl", **params)
        return result.get("nzo_ids", [])

    def find_history_by_nzo_id(self, nzo_id):
        """One specific job by SABnzbd's own id -- what
        download_sources.SabnzbdSource matches against Whisparr's
        `downloadId` (the same value, see whisparr_client.py's
        find_grabbed_download_id()). SABnzbd's `mode=history` accepts a
        `nzo_ids` filter directly rather than needing a client-side
        scan of the whole history."""
        slots = self._call("history", nzo_ids=nzo_id).get("history", {}).get("slots", [])
        return slots[0] if slots else None
