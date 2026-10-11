"""Prowlarr API client.

Confirmed live against a real instance (2026-09-20, see PROGRESS.md /
project_roadmap_todo.md memory): `GET /api/v1/system/status` with the
standard `X-Api-Key` header (same convention as Whisparr/Stash)
authenticates, and `GET /api/v1/search` works fully standalone (no
Sonarr/Radarr/Whisparr wrapper needed) -- searching indexers directly,
wired into indexer_search.py's own candidate selection (2026-09-22),
replaces Whisparr's own `/release?movieId=X` when Prowlarr is
configured (see that module's docstring for why: Whisparr's own
internal search-term construction was found live to be the actual
source of this project's title-mismatch bug class, not the indexers or
Prowlarr themselves).
"""
import requests


class ProwlarrClient:
    def __init__(self, url, api_key, timeout=15):
        self.base = f"{url.rstrip('/')}/api/v1"
        self.session = requests.Session()
        self.session.headers["X-Api-Key"] = api_key
        self.timeout = timeout

    def ping(self):
        resp = self.session.get(f"{self.base}/system/status", timeout=self.timeout)
        resp.raise_for_status()

    @staticmethod
    def _is_xxx(item):
        """True when a Prowlarr result is in the XXX range (6000-6999: 6000 XXX, 6040 x264, 6045 UHD, 6070 Other ...) -- or carries no
        category at all. Prowlarr's own extra ids (1xxxxx = the indexer's native category) and other ranges (3000 Audio ...) do not count."""
        cats = item.get("categories") or []
        return not cats or any(isinstance(c.get("id"), int) and 6000 <= c["id"] < 7000 for c in cats)

    def search(self, query, categories=(6000,), limit=100):
        """Raw passthrough of Prowlarr's own `/api/v1/search` results --
        no normalization here (see indexer_search.py's
        normalize_prowlarr_release() for that; this client stays a thin
        mirror of whisparr_client.py's own get_release(), which is
        equally raw). `type=search` is a plain free-text query, confirmed
        live to need no prior movie/series registration, unlike a
        Sonarr/Radarr/Whisparr-wrapped search.
        XXX scope (6000-6999) everywhere else in this project. A blank query (the RSS feed pull) asks Prowlarr for `categories` as before.
        A free-text query does NOT: found live 2026-10-10, Prowlarr answered `query=Some Studio&categories=6000` with 0 results while the
        same query without a category returned 59 (NZBFinder files its results under 6070 / 6045 / 6040 / 6030, never under the parent
        6000), so the filter is applied here instead, on the categories each result carries."""
        params = {"query": query, "type": "search", "limit": limit}
        blank = not str(query or "").strip()
        if blank:
            params["categories"] = ",".join(str(c) for c in categories)
        resp = self.session.get(f"{self.base}/search", params=params, timeout=self.timeout)
        resp.raise_for_status()
        results = resp.json()
        return results if blank else [r for r in results if self._is_xxx(r)]

    def get_download_clients(self):
        """Prowlarr's own download clients. Prowlarr MASKS api keys and passwords in this answer (stars), so a key can never be taken over from here;
        host/port/ssl/urlBase/category/username can (checked live 2026-10-11)."""
        resp = self.session.get(f"{self.base}/downloadclient", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_indexers(self):
        """Every configured indexer's own `priority` -- same convention
        whisparr_client.py's get_indexers() already uses, against
        Prowlarr's own indexer-id space (different numeric ids than
        Whisparr's, confirmed live to be the same underlying real
        indexers)."""
        resp = self.session.get(f"{self.base}/indexer", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()
