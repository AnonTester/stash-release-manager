"""Maintenance: organize the existing library -- rename / move files to the
naming scheme, like the *arrs' "Organize"/"Rename files" (2026-09-28,
v0.82.0, user decisions):

- Only the Whisparr library folder (lowercase `<root>/scenes/`); Scenes/,
  Movies/, movies/, _VR/ are separate topics.
- Target = the naming scheme (scene_folder_format / scene_naming_format)
  with the scene's OWN studio in StashDB's spelling (not the network).
- Only FINGERPRINT-VERIFIED files are offered: a file is renamed/moved only
  if its fingerprints match the scene it's tagged with (StashDB reverse
  lookup, phash within 8 bits as fallback; ThePornDB by phash). Others go
  to "needs check" -- a wrong tag must not be burned into a file name.
- Scenes with several files are listed separately, never touched.
- Two phases in the UI: renames inside the same folder first, then moves.
- Extras: files with the same name stem (.funscript, .nfo, subtitles) are
  renamed along; when the file is the only video in its folder, the whole
  folder content (folder.jpg, cover, ...) moves with it and the empty old
  folder is removed.
- Byte-identical duplicates (same oshash elsewhere in the library) are
  processed one at a time with their own scan -- in one batch Stash may
  swap them between scenes (seen 2026-09-28).
- Stash keeps the scene when a file is renamed or moved (tested on a
  throwaway scene 2026-09-28): the target folders are scanned afterwards
  and every scene is checked at its new path. Everything goes to History.

Quality part of the new name: the source tag found in the current name
(WEBDL, WEBRip, Bluray, ...) or WEBDL; the resolution from the file
(below 480 -> "480p" like the library); VR files keep the resolution
token they already have (open question whether to switch them to the
width class).
"""
import logging
import os
import re
import shutil
import time
from collections import defaultdict

import file_naming
import history
import quality_detect

logger = logging.getLogger(__name__)

# Stash's own default video extensions (m4v, mp4, mov, wmv, avi, mpg, mpeg, rmvb, rm, flv, asf, mkv, webm, f4v) plus .ts; .asf & co. were
# missing, so the import counted them as "unknown" files (and "Finish source" refused while they lay there).
VIDEO_EXT = {".mp4", ".mkv", ".avi", ".wmv", ".mov", ".m4v", ".mpg", ".mpeg", ".flv", ".webm", ".ts", ".asf", ".rmvb", ".rm", ".f4v"}
_SOURCE_RE = re.compile(r"(WEBDL|WEB-DL|WEBRip|Bluray|BluRay|HDTV|DVD|Remux)", re.IGNORECASE)
_SOURCE_NAME = {"webdl": "WEBDL", "web-dl": "WEBDL", "webrip": "WEBRip", "bluray": "Bluray", "hdtv": "HDTV",
                "dvd": "DVD", "remux": "Remux"}
_RES_TOKEN_RE = re.compile(r"(?<![0-9A-Za-z])(\d{3,4}p|\dK)(?![0-9A-Za-z])")
PHASH_TOLERANCE = 8
_FP_BATCH = 40

_SCENES_QUERY = """
query($f: FindFilterType, $s: SceneFilterType) {
  findScenes(filter: $f, scene_filter: $s) {
    scenes {
      id title date
      studio { id name stash_ids { endpoint stash_id } }
      tags { name }
      stash_ids { endpoint stash_id }
      files { id path width height duration mod_time fingerprints { type value } }
    }
  }
}
"""

_overview_cache = {"at": 0, "data": None}


def _older_than_release(file, scene_date, slack_days=60):
    from datetime import datetime, timedelta
    try:
        modified = datetime.fromisoformat((file.get("mod_time") or "").replace("Z", "+00:00")).date()
        released = datetime.fromisoformat(scene_date).date()
    except (TypeError, ValueError):
        return False
    return modified < released - timedelta(days=slack_days)


class Organizer:
    def __init__(self, db, stash_client, stashdb_client, theporndb_client, to_local, settings_getter,
                 get_whisparr_client=None):
        self.get_whisparr = get_whisparr_client
        self.db = db
        self.stash = stash_client
        self.stashdb = stashdb_client
        self.theporndb = theporndb_client
        self.to_local = to_local
        self.setting = settings_getter  # (key) -> value

    # ---------- expected path ----------

    def _root(self):
        return (self.setting("whisparr_root_folder_path") or "/data").rstrip("/")

    def _in_scope(self, path):
        return path.startswith(self._root() + "/scenes/")

    def _quality(self, scene, file, name):
        m = _SOURCE_RE.search(name)
        source = _SOURCE_NAME[m.group(1).lower()] if m else quality_detect.DEFAULT_SOURCE
        width, height = file.get("width") or 0, file.get("height") or 0
        is_vr = any((t.get("name") or "").lower() == "vr" for t in scene.get("tags") or [])
        if is_vr or quality_detect.vr_label(width, height):
            token = _RES_TOKEN_RE.search(re.sub(r"\.[A-Za-z0-9]+$", "", name))
            resolution = token.group(1) if token else quality_detect.vr_label(width, height, True) \
                or quality_detect.resolution_label(height)
        else:
            resolution = quality_detect.resolution_label(height)
        return f"{source}-{resolution}"

    def expected_path(self, scene, file, studio_name):
        name = file["path"].rsplit("/", 1)[1]
        ext = os.path.splitext(name)[1]
        detail = {"title": scene["title"], "date": scene["date"], "studio": {"name": studio_name},
                  "performers": []}
        tokens = file_naming.build_tokens(detail, self._quality(scene, file, name), tags=[t.get("name") for t in scene.get("tags") or []],
                                          options=file_naming.naming_options(lambda key: self.setting(key) or "[]"))
        rel = file_naming.render_path(self.setting("scene_folder_format"), self.setting("scene_naming_format"),
                                      tokens, ext)
        return f"{self._root()}/{rel.lstrip('/')}"

    # ---------- overview (no fingerprints) ----------

    def _all_scenes(self):
        return self.stash._graphql(_SCENES_QUERY, {"f": {"per_page": -1}})["findScenes"]["scenes"]

    def overview(self, refresh=False):
        """Per studio: how many files would be renamed / moved (by the
        LOCAL studio name -- the exact StashDB spelling and the fingerprint
        check come with the per-studio plan). Cached 10 minutes."""
        if not refresh and _overview_cache["data"] and time.time() - _overview_cache["at"] < 600:
            return _overview_cache["data"]
        per = defaultdict(lambda: {"rename": 0, "move": 0})
        names = {}
        for scene in self._all_scenes():
            if not (scene["stash_ids"] and scene["title"] and scene["date"] and scene["studio"]):
                continue
            if len(scene["files"]) != 1 or not self._in_scope(scene["files"][0]["path"]):
                continue
            file = scene["files"][0]
            expected = self.expected_path(scene, file, scene["studio"]["name"])
            if expected == file["path"]:
                continue
            sid = scene["studio"]["id"]
            names[sid] = scene["studio"]["name"]
            same_folder = expected.rsplit("/", 1)[0] == file["path"].rsplit("/", 1)[0]
            per[sid]["rename" if same_folder else "move"] += 1
        data = sorted(({"studio_id": k, "studio": names[k], **v, "total": v["rename"] + v["move"]}
                       for k, v in per.items()), key=lambda x: (-x["total"], x["studio"].lower()))
        _overview_cache.update(at=time.time(), data=data)
        return data

    # ---------- per-studio plan ----------

    def _studio_folder_name(self, studio):
        stashdb_id = next((s["stash_id"] for s in studio.get("stash_ids") or []
                           if s["endpoint"] == self.stash.stashdb_endpoint), None)
        if stashdb_id:
            try:
                return self.stashdb.get_studio_name(stashdb_id) or studio["name"]
            except Exception:
                logger.exception("organize: StashDB name for studio %s", studio["name"])
        return studio["name"]

    def _oshash_counts(self):
        counts = defaultdict(int)
        for scene in self._all_scenes():
            for f in scene["files"]:
                for fp in f["fingerprints"]:
                    if fp["type"] == "oshash":
                        counts[fp["value"]] += 1
        return counts

    def _verify(self, candidates):
        """{file_id: (verified, how)} for [(scene, file)]."""
        result = {}
        stashdb_pairs = []
        for scene, file in candidates:
            ids = {s["endpoint"]: s["stash_id"] for s in scene["stash_ids"]}
            if self.stash.stashdb_endpoint in ids:
                stashdb_pairs.append((scene, file, ids[self.stash.stashdb_endpoint]))
            elif self.stash.theporndb_endpoint in ids and self.theporndb is not None:
                result[file["id"]] = self._verify_by_phash(self.theporndb, ids[self.stash.theporndb_endpoint], file)
            else:
                result[file["id"]] = (False, "no StashDB/ThePornDB id")
        for i in range(0, len(stashdb_pairs), _FP_BATCH):
            chunk = stashdb_pairs[i:i + _FP_BATCH]
            sets = [[{"hash": fp["value"], "algorithm": fp["type"].upper()} for fp in f["fingerprints"]]
                    for _, f, _ in chunk]
            try:
                matches = self.stashdb.find_scenes_by_fingerprints(sets)
            except Exception:
                logger.exception("organize: StashDB fingerprint lookup failed")
                matches = [None] * len(chunk)
            for (scene, file, stashdb_id), found in zip(chunk, matches):
                if found and any(m.get("id") == stashdb_id for m in found):
                    result[file["id"]] = (True, "fingerprint")
                else:
                    result[file["id"]] = self._verify_by_phash(self.stashdb, stashdb_id, file,
                                                               other=[m.get("title") for m in found or []])
        return result

    def _provider_metadata(self, scene):
        """{title, date} of the scene at StashDB (or ThePornDB), or None."""
        ids = {s["endpoint"]: s["stash_id"] for s in scene["stash_ids"]}
        for endpoint, client in ((self.stash.stashdb_endpoint, self.stashdb),
                                 (self.stash.theporndb_endpoint, self.theporndb)):
            if endpoint in ids and client is not None:
                try:
                    detail = client.get_scene_detail(ids[endpoint])
                except Exception:
                    logger.exception("organize: metadata lookup failed for scene %s", scene["id"])
                    return None
                if detail and detail.get("title") and detail.get("date"):
                    return {"title": detail["title"], "date": detail["date"]}
                return None
        return None

    def _verify_by_phash(self, client, provider_scene_id, file, other=None):
        local = next((fp["value"] for fp in file["fingerprints"] if fp["type"] == "phash"), None)
        try:
            remote = [fp["hash"] for fp in client.get_scene_fingerprints(provider_scene_id)
                      if fp.get("algorithm", "").lower() == "phash" and fp.get("hash")]
        except Exception:
            return (False, "fingerprint lookup failed")
        if not remote:
            return (False, "the provider has no fingerprints for this scene")
        if not local:
            return (False, "no local phash yet")
        best = min(bin(int(h, 16) ^ int(local, 16)).count("1") for h in remote)
        if best <= PHASH_TOLERANCE:
            return (True, f"phash (distance {best})")
        hint = f"; matches {other[0]!r} instead" if other else ""
        return (False, f"fingerprints don't match (phash distance {best}{hint})")

    def plan(self, studio_id):
        """Per-studio plan: {studio, rename, move, needs_check, multi_file, conflict}."""
        scenes = self.stash._graphql(_SCENES_QUERY, {
            "f": {"per_page": -1},
            "s": {"studios": {"value": [str(studio_id)], "modifier": "INCLUDES"}},
        })["findScenes"]["scenes"]
        scenes = [s for s in scenes if s["studio"] and str(s["studio"]["id"]) == str(studio_id)]
        if not scenes:
            return {"studio": None, "rename": [], "move": [], "needs_check": [], "multi_file": [], "conflict": []}
        folder_studio = self._studio_folder_name(scenes[0]["studio"])
        dup = self._oshash_counts()
        plan = {"studio": folder_studio, "rename": [], "move": [], "needs_check": [], "multi_file": [], "conflict": []}
        candidates = []
        for scene in scenes:
            if not (scene["stash_ids"] and scene["title"] and scene["date"]):
                continue
            files = [f for f in scene["files"] if self._in_scope(f["path"])]
            if not files:
                continue
            expected = [self.expected_path(scene, f, folder_studio) for f in files]
            if all(e == f["path"] for e, f in zip(expected, files)):
                continue
            if len(scene["files"]) > 1:
                plan["multi_file"].append({"scene_id": scene["id"], "title": scene["title"],
                                           "paths": [f["path"] for f in scene["files"]]})
                continue
            candidates.append((scene, files[0], expected[0]))
        verified = self._verify([(s, f) for s, f, _ in candidates])
        for scene, file, expected in candidates:
            ok, how = verified.get(file["id"], (False, "not checked"))
            fresh = None
            if ok:
                # Title/date from the provider, like an import (2026-09-28:
                # some Stash scenes carry the file name as title and a scan
                # date -- "Blow Job Frenzy.flv - 2024-08-16").
                fresh = self._provider_metadata(scene)
                if fresh:
                    expected = self.expected_path({**scene, **fresh}, file, folder_studio)
                # A phash NEAR-match is weaker evidence (low-res videos with the
                # same studio intro can land within a few bits): a file older
                # than the scene's release can't be that scene (2026-09-28: a
                # 2008 Dancing Bear .flv "matched" a 2024 scene at distance 4).
                # (an EXACT phash match, distance 0, is the same video -- an old rip
                # of a scene the provider dated later; 2026-09-29, 19 such files)
                if not how.startswith("fingerprint") and not how.startswith("phash (distance 0)") \
                        and _older_than_release(file, (fresh or scene).get("date")):
                    ok, how = False, f"{how}, but the file is older than the scene's release date"
                if expected == file["path"]:
                    continue
            oshash = next((fp["value"] for fp in file["fingerprints"] if fp["type"] == "oshash"), None)
            stashdb_id = next((s["stash_id"] for s in scene["stash_ids"]
                               if s["endpoint"] == self.stash.stashdb_endpoint), None)
            item = {"scene_id": scene["id"], "file_id": file["id"], "title": (fresh or scene)["title"],
                    "stashdb_id": stashdb_id, "date": (fresh or scene).get("date"),
                    "path": file["path"], "new_path": expected, "check": how,
                    "duplicate": bool(oshash and dup.get(oshash, 0) > 1)}
            if not ok:
                plan["needs_check"].append(item)
            elif os.path.exists(self.to_local(expected)):
                # 2026-09-28: two versions of one scene in a folder -- the
                # target name is taken; the user decides which one to keep.
                plan["conflict"].append({**item, "reason": "target name already exists"})
            elif expected.rsplit("/", 1)[0] == file["path"].rsplit("/", 1)[0]:
                plan["rename"].append(item)
            else:
                plan["move"].append(item)
        # Two planned files that want the same target name also collide.
        by_target = defaultdict(list)
        for key in ("rename", "move"):
            for x in plan[key]:
                by_target[x["new_path"].lower()].append((key, x))
        for group in by_target.values():
            if len(group) > 1:
                for key, x in group:
                    plan[key].remove(x)
                    plan["conflict"].append({**x, "reason": "another file of this plan has the same target name"})
        for key in ("rename", "move", "needs_check", "conflict"):
            plan[key].sort(key=lambda x: x["path"].lower())
        return plan

    # ---------- apply ----------

    def _move_one(self, item):
        """Moves/renames the video and its extras on disk. Returns the old
        local folder (for the empty-folder cleanup)."""
        old_local, new_local = self.to_local(item["path"]), self.to_local(item["new_path"])
        if os.path.exists(new_local):
            raise FileExistsError(f"target already exists: {item['new_path']}")
        if not os.path.exists(old_local):
            raise FileNotFoundError(f"file not found on disk: {item['path']}")
        old_dir, old_name = os.path.split(old_local)
        new_dir, new_name = os.path.split(new_local)
        old_stem, new_stem = os.path.splitext(old_name)[0], os.path.splitext(new_name)[0]
        siblings = [n for n in os.listdir(old_dir) if n != old_name]
        other_videos = [n for n in siblings if os.path.splitext(n)[1].lower() in VIDEO_EXT]
        os.makedirs(new_dir, exist_ok=True)
        shutil.move(old_local, new_local)
        moved = []
        for n in siblings:
            src = os.path.join(old_dir, n)
            same_stem = n.startswith(old_stem + ".")
            action = self._extra_action(n, same_stem, has_other_videos=bool(other_videos),
                                        folder_changes=old_dir != new_dir)
            if action == "move":
                if not os.path.isfile(src):
                    continue  # NEVER drag a folder along (2026-09-29: a loose video in a studio folder took all 284 scene folders with it)
                dst = os.path.join(new_dir, new_stem + n[len(old_stem):]) if same_stem else os.path.join(new_dir, n)
                if os.path.exists(dst):
                    continue
                shutil.move(src, dst)
                moved.append(n)
            elif action == "recycle" and os.path.isfile(src):
                self.recycle(src)
        item["extras_moved"] = moved
        return old_dir

    def _record_move(self, old_path, new_path):
        """Tables of this sidecar that are keyed by file path must follow a moved/renamed file
        (2026-09-30: after the phase-2 moves, upgrade candidates and VR-check results still pointed at
        the old paths -- an upgrade silently failed to merge)."""
        try:
            with self.db.connect() as conn:
                conn.execute("UPDATE OR IGNORE vr_file_check SET file_path = ? WHERE file_path = ?", (new_path, old_path))
                conn.execute("UPDATE OR IGNORE upgrade_candidate SET file_path = ? WHERE file_path = ?", (new_path, old_path))
        except Exception:
            logger.exception("organize: could not update path-keyed rows for %s", old_path)

    def _extra_action(self, name, same_stem, has_other_videos, folder_changes):
        """What happens to a non-video file next to a moved video: 'move',
        'recycle' or 'leave'. Organize: a same-stem extra is renamed along; folder
        content (folder.jpg, ...) follows the only video."""
        if same_stem:
            return "move"
        if not has_other_videos and folder_changes:
            return "move"
        return "leave"

    def recycle(self, local_path):  # set by the Importer; the plain Organizer never recycles
        raise NotImplementedError

    def _cleanup_roots(self):
        """Local folders below which empty folders are removed (the roots
        themselves stay)."""
        return [self.to_local(self._root() + "/scenes")]

    def _cleanup_dirs(self, dirs):
        roots = self._cleanup_roots()
        for d in sorted(set(dirs), key=len, reverse=True):
            cur = d
            while cur and any(cur.startswith(r + "/") for r in roots) and os.path.isdir(cur):
                try:
                    os.rmdir(cur)  # only succeeds when empty
                except OSError:
                    break
                cur = os.path.dirname(cur)

    def queue_guard(self, force=False):
        """None when it is fine to move files; else {"error", "queue"}. Moves are followed by a Stash scan,
        and Stash runs one job at a time -- behind a Generate task (hours) the scan, and with it every
        confirmation, would wait for hours (2026-09-30)."""
        if force:
            return None
        try:
            queue = self.stash._graphql("{ jobQueue { id status description progress } }", {})["jobQueue"] or []
        except Exception:
            logger.exception("organize: couldn't read Stash's job queue")
            return None
        active = [j for j in queue if j["status"] in ("RUNNING", "READY")]
        generating = [j for j in active if "enerat" in (j.get("description") or "")]
        if generating or len(active) >= 6:
            return {"error": f"Stash's job queue is busy ({len(active)} job(s){'; a Generate task is running' if generating else ''}) "
                             "-- the scan after the moves would wait behind it. Stop the Generate task (Stash > Settings > "
                             "Tasks) or try again when the queue is empty.",
                    "queue": [f"{j['description']} ({j['status']})" for j in active][:8]}
        return None

    def _scan_and_wait(self, folders):
        if not folders:
            return
        job = self.stash.scan_paths(sorted(set(folders)))
        try:
            self.stash.wait_for_job(job, poll_interval=5, timeout=6 * 3600)
        except Exception:
            logger.exception("organize: Stash scan didn't finish in time")
        time.sleep(2)

    def sync_whisparr(self, item):
        """Tells Whisparr about a renamed/moved file (user, 2026-09-28: keep
        Whisparr usable in case it's brought back into the process): a new
        folder is set on the movie first (moveFiles=false, Whisparr moves
        nothing), then RescanMovie takes the file over in place. Returns a
        short status; never raises."""
        if not self.get_whisparr or not item.get("stashdb_id"):
            return "not in Whisparr (no StashDB id)"
        try:
            whisparr = self.get_whisparr()
            movie = whisparr.find_by_stash_id(item["stashdb_id"])
            if movie is None:
                return "not in Whisparr"
            new_folder = item["new_path"].rsplit("/", 1)[0]
            if (movie.get("path") or "").rstrip("/") != new_folder:
                owner = next((m for m in whisparr.movies_in_folder(new_folder) if m["id"] != movie["id"]), None)
                if owner is not None:
                    # The folder already belongs to ANOTHER Whisparr movie: the
                    # Stash tag and Whisparr disagree about what this file is
                    # (found live: a file of one title tagged as
                    # another title). Don't move Whisparr's movie --
                    # let the folder's owner take the file and report it.
                    whisparr.rescan_movie(owner["id"])
                    history.add("warning", "history", "organize",
                                f"Tag conflict: the Stash tag says {movie.get('title')!r}, Whisparr has this folder "
                                f"as {owner.get('title')!r} -- check the tag (Library Check)",
                                scene_title=item["title"], details={"folder": new_folder})
                    return f"tag conflict: Whisparr has this folder as {owner.get('title')!r}"
                whisparr.set_movie_path(movie["id"], new_folder)
            status = whisparr.rescan_movie(movie["id"])
            after = whisparr.get_movie(movie["id"])
            known = ((after.get("movieFile") or {}).get("relativePath") or "")
            if known == item["new_path"].rsplit("/", 1)[1]:
                return "updated"
            history.add("warning", "history", "organize", f"Whisparr didn't take over the new file ({status})",
                        scene_title=item["title"], details={"whisparr_movie": movie["id"], "known": known})
            return f"not updated ({status}, knows {known or 'no file'})"
        except Exception as e:
            logger.exception("organize: Whisparr update failed for %s", item["new_path"])
            return f"Whisparr update failed: {e}"

    def apply(self, studio_id, file_ids, kinds=("rename", "move"), force=False):
        """Recomputes the plan (never trusts the browser's paths) and
        applies the requested verified items. Returns {done, failed}."""
        busy = self.queue_guard(force)
        if busy:
            return busy
        wanted = {str(i) for i in file_ids or []}
        plan = self.plan(studio_id)
        items = [i for k in kinds for i in plan.get(k, []) if str(i["file_id"]) in wanted]
        done, failed, old_dirs, batch_folders = [], [], [], []
        for item in sorted(items, key=lambda x: x["duplicate"]):
            try:
                old_dirs.append(self._move_one(item))
            except Exception as e:
                failed.append({**item, "reason": str(e)})
                continue
            if item["duplicate"]:
                # Byte-identical copy elsewhere: scan right away, alone.
                self._scan_and_wait([item["new_path"].rsplit("/", 1)[0]])
            else:
                batch_folders.append(item["new_path"].rsplit("/", 1)[0])
            done.append(item)
        self._scan_and_wait(batch_folders)
        confirmed = []
        for item in done:
            scene = self.stash.find_scene_by_path(item["new_path"])
            if scene is not None and str(scene["id"]) == str(item["scene_id"]):
                item["whisparr"] = self.sync_whisparr(item)
                self._record_move(item["path"], item["new_path"])
                confirmed.append(item)
                history.add("info", "history", "organize",
                            f"{'Moved' if item['path'].rsplit('/', 1)[0] != item['new_path'].rsplit('/', 1)[0] else 'Renamed'}: "
                            f"{item['path'].rsplit('/', 1)[1]} -> {item['new_path'][len(self._root()):]}",
                            scene_title=item["title"],
                            details={"stash_scene": item["scene_id"], "old": item["path"], "new": item["new_path"],
                                     "extras": item.get("extras_moved")})
            else:
                failed.append({**item, "reason": "moved on disk, but Stash doesn't show the scene there yet"})
                history.add("warning", "history", "organize", "Moved on disk, Stash not updated yet (rescan)",
                            scene_title=item["title"], details={"old": item["path"], "new": item["new_path"]})
        self._cleanup_dirs(old_dirs)
        if confirmed or failed:
            history.event("warning" if failed else "info", "organize",
                          f"Organize {plan['studio']}: {len(confirmed)} done, {len(failed)} failed")
        _overview_cache["data"] = None
        return {"done": confirmed, "failed": failed}
