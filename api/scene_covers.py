"""Scene covers for the release lists (v0.254.0).

A scene that is not in Stash yet shows the cover of its stash-box (StashDB, ThePornDB, an added stash-box). The sidecar fetches it once, cuts
it to a small JPEG and keeps it under <data>/covers/<provider>/<scene id>.jpg; the browser gets it as a data: URL through the plugin's normal
operations (no connection from the browser to the sidecar is needed, so it also works on an https Stash page). As soon as the scene is in Stash
the cover is Stash's own (`/scene/<id>/screenshot`, answered as {"stash": <local id>}) and the cached copy is deleted: nothing is kept twice.

A scene whose stash-box has no image is remembered with a `.none` marker for a day so the list does not ask the stash-box on every view."""
import base64
import logging
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import release_repo

logger = logging.getLogger(__name__)

COVER_DIR = "covers"
WIDTH = 320
NONE_RETRY_S = 24 * 3600
MAX_FETCHES = 40        # per request; the rest is answered {"pending": True} and asked for again by the page
WORKERS = 4
MAX_BYTES = 15 * 1024 * 1024
FETCH_TIMEOUT = 15


def _safe(part):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(part))[:120]


def download(url):
    """The image bytes behind a URL (bounded size and time); raises when it is not an image."""
    resp = requests.get(url, timeout=FETCH_TIMEOUT, stream=True, headers={"User-Agent": "StashReleaseManager"})
    resp.raise_for_status()
    if "image" not in (resp.headers.get("Content-Type") or "image"):
        raise ValueError("not an image")
    data = resp.raw.read(MAX_BYTES + 1, decode_content=True)
    if len(data) > MAX_BYTES:
        raise ValueError("image too large")
    return data


def make_thumb(data):
    """A WIDTH px wide JPEG of any image (ffmpeg, already in the image for the file checks)."""
    res = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", "pipe:0", "-vf", f"scale='min({WIDTH},iw)':-2", "-frames:v", "1",
                          "-q:v", "4", "-f", "image2", "-c:v", "mjpeg", "pipe:1"], input=data, capture_output=True, timeout=30)
    if res.returncode != 0 or not res.stdout:
        raise RuntimeError((res.stderr or b"ffmpeg gave no image")[:200].decode("utf-8", "replace"))
    return res.stdout


class SceneCovers:
    def __init__(self, db, data_dir, get_stash_client, get_provider_client, fetch=download, thumb=make_thumb, now=time.time):
        self.db, self.root = db, os.path.join(data_dir, COVER_DIR)
        self.get_stash_client, self.get_provider_client = get_stash_client, get_provider_client
        self.fetch, self.thumb, self.now = fetch, thumb, now

    # -- files
    def path(self, provider, scene_id):
        return os.path.join(self.root, _safe(provider), _safe(scene_id) + ".jpg")

    def _none_path(self, provider, scene_id):
        return self.path(provider, scene_id)[:-4] + ".none"

    def _read(self, provider, scene_id):
        try:
            with open(self.path(provider, scene_id), "rb") as fh:
                return "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode("ascii")
        except OSError:
            return None

    def _write(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)

    def _mark_none(self, provider, scene_id):
        path = self._none_path(provider, scene_id)
        self._write(path, b"")
        os.utime(path, (self.now(), self.now()))

    def _none_fresh(self, provider, scene_id):
        try:
            return self.now() - os.path.getmtime(self._none_path(provider, scene_id)) < NONE_RETRY_S
        except OSError:
            return False

    def purge(self, provider, scene_id):
        for p in (self.path(provider, scene_id), self._none_path(provider, scene_id)):
            try:
                os.remove(p)
            except OSError:
                pass

    # -- the source
    def source_url(self, provider, scene_id):
        url = release_repo.image_url_for_scene(self.db, provider, scene_id)
        if url:
            return url
        client = self.get_provider_client(provider) if self.get_provider_client else None
        if client is None:
            return None
        if provider == "theporndb" and hasattr(client, "get_scene_detail"):
            detail = client.get_scene_detail(scene_id) or {}  # the mirrored copy, not the source site's hotlink
            return detail.get("image_url")
        return client.fetch_scene_image(scene_id) if hasattr(client, "fetch_scene_image") else None

    def _fetch_one(self, provider, scene_id):
        """Fetches and stores one cover; the data: URL, or None (and a `.none` marker when the stash-box has no image)."""
        try:
            url = self.source_url(provider, scene_id)
            if not url:
                self._mark_none(provider, scene_id)
                return None
            self._write(self.path(provider, scene_id), self.thumb(self.fetch(url)))
            return self._read(provider, scene_id)
        except Exception as e:
            logger.info("cover of %s %s: %s", provider, scene_id, e)
            try:
                self._mark_none(provider, scene_id)  # a broken or blocked image is retried tomorrow, not on every view
            except OSError:
                pass
            return None

    # -- the answer for one page of a list
    def covers_for(self, scenes):
        """{"provider:scene id": {"stash": local id} | {"data": data URL} | {"pending": True} | None} for [(provider, scene id)]."""
        pairs = list(dict.fromkeys((p, s) for p, s in scenes if s))
        local = {}
        if pairs:
            try:
                local = self.get_stash_client().local_scene_ids_by_stash_ids(pairs) or {}
            except Exception:
                logger.warning("covers: Stash could not be asked, using the stash-box covers", exc_info=True)
        out, to_fetch = {}, []
        for pair in pairs:
            key = f"{pair[0]}:{pair[1]}"
            if pair in local:
                self.purge(*pair)
                out[key] = {"stash": local[pair]}
                continue
            data = self._read(*pair)
            if data:
                out[key] = {"data": data}
            elif self._none_fresh(*pair):
                out[key] = None
            else:
                to_fetch.append(pair)
        batch, rest = to_fetch[:MAX_FETCHES], to_fetch[MAX_FETCHES:]
        if batch:
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                for pair, data in zip(batch, pool.map(lambda p: self._fetch_one(*p), batch)):
                    out[f"{pair[0]}:{pair[1]}"] = {"data": data} if data else None
        for pair in rest:
            out[f"{pair[0]}:{pair[1]}"] = {"pending": True}
        return out

    def sweep(self):
        """Removes the cached covers of scenes that have no release row any more; returns how many files went."""
        keep = {(_safe(p), _safe(s)) for p, s in release_repo.scene_keys(self.db)}
        removed = 0
        if not os.path.isdir(self.root):
            return 0
        for prov in os.listdir(self.root):
            folder = os.path.join(self.root, prov)
            if not os.path.isdir(folder):
                continue
            for name in os.listdir(folder):
                scene = name.rsplit(".", 1)[0]
                if name.endswith(".tmp") or (prov, scene) not in keep:
                    try:
                        os.remove(os.path.join(folder, name))
                        removed += 1
                    except OSError:
                        pass
        return removed
