"""Pictures of scenes, performers and studios for the lists and the Search page (scenes v0.254.0, performers and studios v0.256.0).

An item that is not in Stash shows the picture of its stash-box (StashDB, ThePornDB, an added stash-box). The sidecar fetches it once, scales it
down and keeps it under <data>/covers/<kind>/<provider>/<id>.<jpg|png>; the browser gets it as a data: URL through the plugin's normal operations
(no connection from the browser to the sidecar or to the provider is needed, so it also works on an https page behind a reverse proxy). As soon
as the item is in Stash the picture is Stash's own (answered as {"stash": <local id>}; the plugin builds the path) and the cached copy is deleted.

Sizes: scenes 320 px wide JPEG, performers 240 px wide JPEG, studios fit into 256 px as PNG (logos are often transparent, JPEG would make
that black). An item whose stash-box has no image (or blocks it) is remembered with a `.none` marker for a day.

Where the provider's address comes from: never from the browser. For a scene any release row with an `image_url`; for performers, studios and
the scenes of the Search page the addresses the stash-box itself sent with the search results (table `image_hint`, filled by routers/search.py);
failing that the stash-box is asked for that one item."""
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
# kind -> (ffmpeg scale filter, extension, media type, ffmpeg codec)
KINDS = {
    "scene": ("scale='min(320,iw)':-2", "jpg", "image/jpeg", ["-q:v", "4", "-c:v", "mjpeg"]),
    "performer": ("scale='min(240,iw)':-2", "jpg", "image/jpeg", ["-q:v", "4", "-c:v", "mjpeg"]),
    "studio": ("scale='min(256,iw)':'min(256,ih)':force_original_aspect_ratio=decrease", "png", "image/png", ["-c:v", "png"]),
}
NONE_RETRY_S = 24 * 3600
UNUSED_DAYS = 30        # a performer / studio picture nobody asked for this long is removed (it is fetched again when needed)
HINT_DAYS = 30
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


def make_thumb(data, kind="scene"):
    """A small version of any image for `kind` (ffmpeg, already in the image for the file checks)."""
    scale, ext, _, codec = KINDS[kind]
    res = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", "pipe:0", "-vf", scale, "-frames:v", "1", *codec, "-f", "image2", "pipe:1"],
                         input=data, capture_output=True, timeout=30)
    if res.returncode != 0 or not res.stdout:
        raise RuntimeError((res.stderr or b"ffmpeg gave no image")[:200].decode("utf-8", "replace"))
    return res.stdout


def remember_hints(db, kind, provider, items):
    """Keeps the picture addresses a stash-box sent with its search results ([{id, image_url}]) so the picture can be cached later without
    asking the stash-box again. Only http(s) addresses are kept."""
    rows = [(kind, provider, str(i.get("id")), i.get("image_url")) for i in items or []
            if i.get("id") and isinstance(i.get("image_url"), str) and i["image_url"].lower().startswith(("http://", "https://"))]
    if not rows:
        return
    with db.connect() as conn:
        conn.executemany("INSERT OR REPLACE INTO image_hint (kind, provider, entity_id, url, seen_at) VALUES (?, ?, ?, ?, datetime('now'))", rows)


def _hint(db, kind, provider, entity_id):
    with db.connect() as conn:
        row = conn.execute("SELECT url FROM image_hint WHERE kind = ? AND provider = ? AND entity_id = ?", (kind, provider, str(entity_id))).fetchone()
    return row[0] if row else None


class SceneCovers:
    def __init__(self, db, data_dir, get_stash_client, get_provider_client, fetch=download, thumb=make_thumb, now=time.time, endpoint_of=None):
        self.db, self.root = db, os.path.join(data_dir, COVER_DIR)
        self.get_stash_client, self.get_provider_client = get_stash_client, get_provider_client
        self.fetch, self.thumb, self.now, self.endpoint_of = fetch, thumb, now, endpoint_of
        self._migrate_scene_folders()

    def _migrate_scene_folders(self):
        """Scene covers lived in covers/<provider>/ before the kinds existed (0.254.0); they move to covers/scene/<provider>/."""
        try:
            for name in os.listdir(self.root):
                full = os.path.join(self.root, name)
                if name not in KINDS and os.path.isdir(full):
                    os.makedirs(os.path.join(self.root, "scene"), exist_ok=True)
                    os.replace(full, os.path.join(self.root, "scene", name))
        except OSError:
            pass

    # -- files
    def path(self, kind, provider, entity_id):
        return os.path.join(self.root, kind, _safe(provider), f"{_safe(entity_id)}.{KINDS[kind][1]}")

    def _none_path(self, kind, provider, entity_id):
        return os.path.splitext(self.path(kind, provider, entity_id))[0] + ".none"

    def _read(self, kind, provider, entity_id):
        path = self.path(kind, provider, entity_id)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            if kind != "scene":
                os.utime(path, (self.now(), self.now()))  # "used": the sweep keeps what is still looked at
            return f"data:{KINDS[kind][2]};base64," + base64.b64encode(data).decode("ascii")
        except OSError:
            return None

    def _write(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)

    def _mark_none(self, kind, provider, entity_id):
        path = self._none_path(kind, provider, entity_id)
        self._write(path, b"")
        os.utime(path, (self.now(), self.now()))

    def _none_fresh(self, kind, provider, entity_id):
        try:
            return self.now() - os.path.getmtime(self._none_path(kind, provider, entity_id)) < NONE_RETRY_S
        except OSError:
            return False

    def purge(self, kind, provider, entity_id):
        for p in (self.path(kind, provider, entity_id), self._none_path(kind, provider, entity_id)):
            try:
                os.remove(p)
            except OSError:
                pass

    # -- the source
    def source_url(self, kind, provider, entity_id):
        url = release_repo.image_url_for_scene(self.db, provider, entity_id) if kind == "scene" else None
        url = url or _hint(self.db, kind, provider, entity_id)
        if url:
            return url
        client = self.get_provider_client(provider) if self.get_provider_client else None
        if client is None:
            return None
        if kind == "scene" and provider == "theporndb" and hasattr(client, "get_scene_detail"):
            detail = client.get_scene_detail(entity_id) or {}  # the mirrored copy, not the source site's hotlink
            return detail.get("image_url")
        fetch = getattr(client, f"fetch_{kind}_image", None)
        return fetch(entity_id) if fetch else None

    def _fetch_one(self, kind, provider, entity_id):
        """Fetches and stores one picture; the data: URL, or None (and a `.none` marker when the stash-box has no image)."""
        try:
            url = self.source_url(kind, provider, entity_id)
            if not url:
                self._mark_none(kind, provider, entity_id)
                return None
            self._write(self.path(kind, provider, entity_id), self.thumb(self.fetch(url), kind))
            return self._read(kind, provider, entity_id)
        except Exception as e:
            logger.info("%s picture of %s %s: %s", kind, provider, entity_id, e)
            try:
                self._mark_none(kind, provider, entity_id)  # a broken or blocked image is retried tomorrow, not on every view
            except OSError:
                pass
            return None

    # -- which of them Stash has
    def _in_stash(self, items):
        """{(kind, provider, id): local id} of the items that are in Stash (and, for a performer / studio, have a picture there)."""
        found = {}
        stash = self.get_stash_client()
        scenes = [(p, i) for k, p, i in items if k == "scene"]
        if scenes:
            for (p, i), local in (stash.local_scene_ids_by_stash_ids(scenes) or {}).items():
                found[("scene", p, i)] = local
        for kind in ("performer", "studio"):
            entities = [(p, i) for k, p, i in items if k == kind]
            if entities and self.endpoint_of:
                for (p, i), local in (stash.local_entity_ids_by_stash_ids(kind, entities, self.endpoint_of) or {}).items():
                    found[(kind, p, i)] = local
        return found

    # -- the answer for one page
    def covers_for(self, items):
        """{"kind:provider:id": {"stash": local id} | {"data": data URL} | {"pending": True} | None} for [(kind, provider, id)]."""
        items = list(dict.fromkeys((k, p, i) for k, p, i in items if i and k in KINDS))
        local = {}
        if items:
            try:
                local = self._in_stash(items)
            except Exception:
                logger.warning("covers: Stash could not be asked, using the stash-box pictures", exc_info=True)
        out, to_fetch = {}, []
        for item in items:
            key = ":".join(item)
            if item in local:
                self.purge(*item)
                out[key] = {"stash": local[item]}
                continue
            data = self._read(*item)
            if data:
                out[key] = {"data": data}
            elif self._none_fresh(*item):
                out[key] = None
            else:
                to_fetch.append(item)
        batch, rest = to_fetch[:MAX_FETCHES], to_fetch[MAX_FETCHES:]
        if batch:
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                for item, data in zip(batch, pool.map(lambda it: self._fetch_one(*it), batch)):
                    out[":".join(item)] = {"data": data} if data else None
        for item in rest:
            out[":".join(item)] = {"pending": True}
        return out

    def covers_for_scenes(self, pairs):
        """The scene-only form (/releases/covers, plugin operation releases_covers): [(provider, id)] -> {"provider:id": ...}."""
        got = self.covers_for([("scene", p, i) for p, i in pairs])
        return {key.split(":", 1)[1]: value for key, value in got.items()}

    def sweep(self):
        """Removes cached scene pictures of scenes without a release row and performer / studio pictures nobody used for UNUSED_DAYS, and the
        old picture addresses; returns how many files went."""
        removed = 0
        keep = {(_safe(p), _safe(s)) for p, s in release_repo.scene_keys(self.db)}
        old = self.now() - UNUSED_DAYS * 86400
        for kind in KINDS:
            base = os.path.join(self.root, kind)
            if not os.path.isdir(base):
                continue
            for prov in os.listdir(base):
                folder = os.path.join(base, prov)
                if not os.path.isdir(folder):
                    continue
                for name in os.listdir(folder):
                    full = os.path.join(folder, name)
                    entity = name.rsplit(".", 1)[0]
                    try:
                        gone = (name.endswith(".tmp") or (kind == "scene" and (prov, entity) not in keep)
                                or (kind != "scene" and os.path.getmtime(full) < old))
                        if gone:
                            os.remove(full)
                            removed += 1
                    except OSError:
                        pass
        with self.db.connect() as conn:
            conn.execute("DELETE FROM image_hint WHERE seen_at < datetime('now', ?)", (f"-{HINT_DAYS} days",))
        return removed
