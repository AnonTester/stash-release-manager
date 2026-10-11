"""qBittorrent WebUI API v2 client (v0.133.0, torrent support stage 1).

The first torrent client type, chosen because it is what the *arr applications default to. Interface shaped like sabnzbd_client.py (ping,
add, queue/info lookups) so the callers can pick a client by `download_client.type`.

Details that matter:
- Login is a POST to /api/v2/auth/login that sets the SID cookie (a requests.Session keeps it): 4.x answers 200 "Ok." / "Fails.", 5.x answers 204
  (empty) / 401 (confirmed live 2026-10-06 with the linuxserver image). Too many failed logins get the IP banned (HTTP 403).
- `torrents/add` does NOT return the new torrent's hash, but the sidecar has to remember it (it is the job id, like SABnzbd's nzo_id). So the hash
  is computed here: from a magnet link's `btih`, or -- the way the *arr applications do it -- the .torrent file is downloaded by this sidecar,
  its info dictionary is hashed (SHA-1 of the bencoded `info`) and the file is uploaded to qBittorrent. That also keeps the indexer's API key
  out of qBittorrent.
- Errors are re-raised WITHOUT the request URL (an indexer link can carry an API key), same rule as newznab_client.py."""
import base64
import hashlib
import re
from urllib.parse import parse_qs, urlparse

import requests

USER_AGENT = "StashReleaseManager/1.0"

# *arr defaults (Radarr/Sonarr/Whisparr qBittorrent client): initial state Start, content layout "Default" (= qBittorrent's own setting), recent/older
# priority "Default", not sequential, no first/last piece priority, no post-import category.
DEFAULT_OPTIONS = {
    "initial_state": "start",       # start | force_start | pause
    "recent_priority": "default",   # default | last | first
    "older_priority": "default",
    "sequential": False,
    "first_last": False,
    "content_layout": "default",    # default | original | subfolder
    "post_import_category": "",
    # *arr "Remote Path Mappings" for this client: the folder qBittorrent writes to, as qBittorrent sees it, and the same folder as the sidecar
    # sees it. Both empty = the same share as the SABnzbd downloads setting (Settings > File Import).
    "remote_path": "",
    "local_path": "",
}

# qBittorrent state strings -> a coarse status the UI/importer uses.
DOWNLOADING = {"downloading", "metaDL", "forcedMetaDL", "stalledDL", "checkingDL", "queuedDL", "allocating", "forcedDL", "checkingResumeData", "moving"}
SEEDING = {"uploading", "stalledUP", "checkingUP", "queuedUP", "forcedUP"}
PAUSED = {"pausedDL", "pausedUP", "stoppedDL", "stoppedUP"}
ERRORED = {"error", "missingFiles"}


class QbittorrentError(Exception):
    pass


class AlreadyAdded(QbittorrentError):
    """qBittorrent already has this torrent (same info hash)."""


# ---- minimal bencode (only to find the raw bytes of the `info` dictionary of a .torrent) ----
def _bdecode(data, i=0):
    """Returns (value, next index). dict keys are bytes."""
    c = data[i:i + 1]
    if c == b"i":
        j = data.index(b"e", i)
        return int(data[i + 1:j]), j + 1
    if c == b"l":
        i += 1
        out = []
        while data[i:i + 1] != b"e":
            v, i = _bdecode(data, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i += 1
        out = {}
        while data[i:i + 1] != b"e":
            k, i = _bdecode(data, i)
            v, i = _bdecode(data, i)
            out[k] = v
        return out, i + 1
    if c.isdigit():
        j = data.index(b":", i)
        n = int(data[i:j])
        return data[j + 1:j + 1 + n], j + 1 + n
    raise ValueError("not a bencoded value")


def torrent_info_hash(torrent_bytes):
    """SHA-1 (hex, lowercase) of the bencoded `info` dictionary of a .torrent file."""
    if not torrent_bytes.startswith(b"d"):
        raise ValueError("not a torrent file")
    i = 1
    while torrent_bytes[i:i + 1] != b"e":
        key, i = _bdecode(torrent_bytes, i)
        start = i
        _, i = _bdecode(torrent_bytes, i)
        if key == b"info":
            return hashlib.sha1(torrent_bytes[start:i]).hexdigest()
    raise ValueError("no info dictionary in the torrent file")


def magnet_info_hash(magnet):
    """Hex info hash of a magnet link (btih is 40 hex or 32 base32 characters), else None."""
    for xt in parse_qs(urlparse(magnet).query).get("xt", []):
        m = re.match(r"urn:btih:([A-Za-z0-9]+)$", xt)
        if not m:
            continue
        h = m.group(1)
        if len(h) == 40:
            return h.lower()
        if len(h) == 32:
            return base64.b32decode(h.upper()).hex()
    return None


class QbittorrentClient:
    def __init__(self, url, username="", password="", category="", options=None, timeout=30):
        self.base = url.rstrip("/")
        self.username, self.password = username, password
        self.category = category or ""
        self.options = {**DEFAULT_OPTIONS, **(options or {})}
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT
        self._logged_in = False

    # ---- plumbing ----
    def _request(self, method, path, **kwargs):
        try:
            resp = self._session.request(method, f"{self.base}/api/v2/{path}", timeout=self.timeout, **kwargs)
        except requests.RequestException as e:
            raise QbittorrentError(f"{type(e).__name__} contacting {self.base}") from None
        return resp

    def login(self):
        resp = self._request("POST", "auth/login", data={"username": self.username, "password": self.password},
                             headers={"Referer": self.base})
        if resp.status_code == 403:
            raise QbittorrentError("HTTP 403 -- qBittorrent banned this address after failed logins, or the host is not allowed")
        if resp.status_code == 401:  # qBittorrent 5.x: a failed login is 401 ...
            raise QbittorrentError("login failed (wrong user name or password?)")
        if not resp.ok:
            raise QbittorrentError(f"HTTP {resp.status_code} from {self.base}")
        # ... and a successful one 204 with an empty body; 4.x answers 200 "Ok." / "Fails."
        if resp.status_code == 200 and resp.text.strip() != "Ok.":
            raise QbittorrentError("login failed (wrong user name or password?)")
        self._logged_in = True

    def _call(self, method, path, **kwargs):
        if not self._logged_in:
            self.login()
        resp = self._request(method, path, **kwargs)
        if resp.status_code == 403:  # session expired
            self.login()
            resp = self._request(method, path, **kwargs)
        if not resp.ok:
            raise QbittorrentError(f"HTTP {resp.status_code} from {self.base} ({path})")
        return resp

    # ---- the interface the rest of the sidecar uses ----
    def ping(self):
        """Authenticated, cheap: the application version ("v4.6.5")."""
        return self._call("GET", "app/version").text.strip()

    def webapi_version(self):
        return self._call("GET", "app/webapiVersion").text.strip()

    def ensure_category(self, category=None):
        category = category if category is not None else self.category
        if category:
            # 409 = it exists already; any other answer is not fatal either (the add creates it if missing)
            try:
                self._request("POST", "torrents/createCategory", data={"category": category, "savePath": ""})
            except QbittorrentError:
                pass

    def add(self, url, name=None, category=None, recent=True):
        """Adds a magnet link or a .torrent URL; returns the info hash (lowercase hex). `recent`: the release is a new one (priority per the
        recent/older priority options)."""
        category = self.category if category is None else category
        opts = self.options
        data = {}
        if category:
            data["category"] = category
            self.ensure_category(category)
        if opts["initial_state"] == "pause":
            data["paused"] = "true"
            data["stopped"] = "true"
        elif opts["initial_state"] == "force_start":
            data["forced"] = "true"
        if opts["sequential"]:
            data["sequentialDownload"] = "true"
        if opts["first_last"]:
            data["firstLastPiecePrio"] = "true"
        layout = {"original": "Original", "subfolder": "Subfolder"}.get(opts["content_layout"])
        if layout:
            data["contentLayout"] = layout
        if url.lower().startswith("magnet:"):
            info_hash = magnet_info_hash(url)
            data["urls"] = url
            files = None
        else:
            try:
                resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=self.timeout, allow_redirects=True)
            except requests.RequestException as e:
                raise QbittorrentError(f"{type(e).__name__} fetching the torrent file") from None
            if not resp.ok:
                raise QbittorrentError(f"HTTP {resp.status_code} fetching the torrent file")
            body = resp.content
            if body.startswith(b"magnet:"):  # some indexers redirect to a magnet
                return self.add(body.decode().strip(), name=name, category=category, recent=recent)
            try:
                info_hash = torrent_info_hash(body)
            except (ValueError, IndexError) as e:
                raise QbittorrentError(f"the indexer's download is not a torrent file ({e})") from None
            files = {"torrents": ((name or "release") + ".torrent", body, "application/x-bittorrent")}
        try:
            r = self._call("POST", "torrents/add", data=data, files=files)
        except QbittorrentError as e:
            if "HTTP 409" in str(e):
                raise AlreadyAdded("qBittorrent already has this torrent") from None
            raise
        if r.text.strip() == "Fails.":  # 4.x answers a duplicate (and an invalid file) with "Fails."
            if info_hash and self.find_torrent(info_hash) is not None:
                raise AlreadyAdded("qBittorrent already has this torrent")
            raise QbittorrentError("qBittorrent refused the torrent (not valid?)")
        if not info_hash:
            raise QbittorrentError("could not work out the torrent's hash")
        priority = opts["recent_priority" if recent else "older_priority"]
        if priority in ("first", "last"):
            try:
                self._call("POST", "torrents/topPrio" if priority == "first" else "torrents/bottomPrio", data={"hashes": info_hash})
            except QbittorrentError:
                pass  # queueing may be switched off in qBittorrent
        return info_hash

    def get_torrents(self, hashes=None, category=None):
        """Raw torrent dicts (name, state, progress 0..1, size, amount_left, eta, content_path, save_path, ratio, seeding_time, ...)."""
        params = {}
        if hashes:
            params["hashes"] = "|".join(hashes)
        category = self.category if category is None else category
        if category:
            params["category"] = category
        return self._call("GET", "torrents/info", params=params).json()

    def find_torrent(self, info_hash):
        found = self.get_torrents(hashes=[info_hash], category="")
        return found[0] if found else None

    def torrent_files(self, info_hash):
        return self._call("GET", "torrents/files", params={"hash": info_hash}).json()

    def set_share_limits(self, info_hash, ratio=None, seed_minutes=None):
        """Per-torrent seed goals (the indexer's seed ratio / seed time). None = use qBittorrent's own global limit (-2)."""
        self._call("POST", "torrents/setShareLimits", data={
            "hashes": info_hash, "ratioLimit": -2 if ratio is None else ratio,
            "seedingTimeLimit": -2 if seed_minutes is None else int(seed_minutes), "inactiveSeedingTimeLimit": -2,
            "shareLimitAction": "Default"})  # 5.x requires the last two (confirmed live, 5.2.4); 4.x ignores them

    def set_category(self, info_hash, category):
        self.ensure_category(category)
        self._call("POST", "torrents/setCategory", data={"hashes": info_hash, "category": category})

    def delete(self, info_hash, delete_files=False):
        self._call("POST", "torrents/delete", data={"hashes": info_hash, "deleteFiles": "true" if delete_files else "false"})


def status_of(torrent):
    """'downloading' | 'seeding' | 'paused' | 'error' | 'done' (finished, no longer seeding) for a qBittorrent torrent dict."""
    state = torrent.get("state") or ""
    if state in ERRORED:
        return "error"
    if state in PAUSED:
        return "done" if (torrent.get("progress") or 0) >= 1 else "paused"
    if state in SEEDING:
        return "seeding"
    return "downloading" if state in DOWNLOADING else (state or "unknown")
