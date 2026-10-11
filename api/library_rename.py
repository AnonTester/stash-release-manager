"""Maintenance: file names with "Unknown" as quality (2026-09-28, v0.81.0).

Three shapes were found in the library (93 files):
- "... Unknown-1080p.mp4"  -- this sidecar's own imports: the release name
  had no source tag (fixed for new imports: quality_detect.DEFAULT_SOURCE);
- "... Unknown.mp4"        -- older Whisparr imports of VR files, quality
  completely unknown;
- "... [Unknown].avi"      -- an older naming scheme.
Only the quality part of the name is replaced (the rest of the name is
the later bulk-rename feature's job): the source from the original
release name when this sidecar grabbed it, else WEBDL (the library's
convention for untagged site releases), the resolution from the file
itself (VR files: width class like "8K").

Renaming is done on disk, then the folder is scanned: Stash recognizes the
file by its fingerprint and keeps the scene (tested 2026-09-28 on a
throwaway scene: same id, rating kept, also across folders).
"""
import logging
import os
import re
import time

import history
import quality_detect

logger = logging.getLogger(__name__)

_PATTERNS = [
    # (regex on the file name, kind)
    (re.compile(r"Unknown-(\d{3,4}p|\dK)(?=[ .\]])"), "source"),
    (re.compile(r" Unknown(?=\.[A-Za-z0-9]+$)"), "whole"),
    (re.compile(r"\[Unknown\]"), "bracket"),
]

_FIND_QUERY = """
query($f: FindFilterType, $s: SceneFilterType) {
  findScenes(filter: $f, scene_filter: $s) {
    scenes { id title tags { name } stash_ids { endpoint stash_id } files { id path width height } }
  }
}
"""


def _grabbed_titles(db):
    """{scene_stashdb_id: grabbed release name} for this sidecar's own grabs."""
    with db.connect() as conn:
        return {r["scene_stashdb_id"]: r["grabbed_title"] for r in conn.execute(
            "SELECT scene_stashdb_id, grabbed_title FROM release WHERE grabbed_title IS NOT NULL")}


def _quality(file, is_vr, source):
    width, height = file.get("width") or 0, file.get("height") or 0
    resolution = quality_detect.vr_label(width, height, is_vr) or quality_detect.resolution_label(height)
    return f"{source}-{resolution}"


def proposals(db, stash_client):
    """Every library file whose name carries "Unknown" as quality, with
    the proposed new name. Files whose new name already exists are
    reported with `conflict` instead of a target."""
    data = stash_client._graphql(_FIND_QUERY, {
        "f": {"per_page": -1},
        "s": {"path": {"value": "Unknown", "modifier": "MATCHES_REGEX"}},
    })
    grabbed = _grabbed_titles(db)
    result = []
    for scene in data["findScenes"]["scenes"]:
        is_vr = any((t.get("name") or "").lower() == "vr" for t in scene.get("tags") or [])
        release_name = next((grabbed[s["stash_id"]] for s in scene.get("stash_ids") or []
                             if s["stash_id"] in grabbed), None)
        source = quality_detect.guess_source(release_name) if release_name else quality_detect.DEFAULT_SOURCE
        for file in scene["files"]:
            folder, name = file["path"].rsplit("/", 1)
            new_name = None
            for pattern, kind in _PATTERNS:
                m = pattern.search(name)
                if not m:
                    continue
                if kind == "source":
                    new_name = name[:m.start()] + f"{source}-{m.group(1)}" + name[m.end():]
                elif kind == "whole":
                    new_name = name[:m.start()] + f" {_quality(file, is_vr, source)}" + name[m.end():]
                else:
                    new_name = name[:m.start()] + f"[{_quality(file, is_vr, source)}]" + name[m.end():]
                break
            if not new_name or new_name == name:
                continue
            result.append({
                "scene_id": scene["id"], "title": scene.get("title") or "", "path": file["path"],
                "new_path": f"{folder}/{new_name}", "width": file.get("width"), "height": file.get("height"),
                "is_vr": is_vr, "source_from_release": bool(release_name),
            })
    return sorted(result, key=lambda r: r["path"].lower())


def apply(db, stash_client, to_local, paths):
    """Renames the given files (a subset of proposals(), matched by
    `path`), then scans their folders and checks every scene points at
    the new path. Returns {renamed, failed, skipped}."""
    wanted = set(paths or [])
    todo = [p for p in proposals(db, stash_client) if p["path"] in wanted]
    renamed, failed, skipped = [], [], []
    for item in todo:
        old_local, new_local = to_local(item["path"]), to_local(item["new_path"])
        if os.path.exists(new_local):
            skipped.append({**item, "reason": "a file with the new name already exists"})
            continue
        try:
            os.rename(old_local, new_local)
            renamed.append(item)
        except OSError as e:
            failed.append({**item, "reason": str(e)})
    folders = sorted({i["new_path"].rsplit("/", 1)[0] for i in renamed})
    if folders:
        job = stash_client.scan_paths(folders)
        try:
            stash_client.wait_for_job(job, timeout=1800)
        except Exception:
            logger.exception("rename: Stash scan of %d folder(s) didn't finish in time", len(folders))
        time.sleep(2)
    confirmed = []
    for item in renamed:
        scene = stash_client.find_scene_by_path(item["new_path"])
        if scene is not None and str(scene["id"]) == str(item["scene_id"]):
            confirmed.append(item)
            try:  # path-keyed rows follow the renamed file (see library_organize.Organizer._record_move)
                with db.connect() as conn:
                    conn.execute("UPDATE OR IGNORE vr_file_check SET file_path = ? WHERE file_path = ?", (item["new_path"], item["path"]))
                    conn.execute("UPDATE OR IGNORE upgrade_candidate SET file_path = ? WHERE file_path = ?", (item["new_path"], item["path"]))
            except Exception:
                logger.exception("rename: could not update path-keyed rows for %s", item["path"])
            history.add("info", "history", "rename", f"Renamed: {item['path'].rsplit('/', 1)[1]} -> "
                        f"{item['new_path'].rsplit('/', 1)[1]}", scene_title=item["title"],
                        details={"stash_scene": item["scene_id"], "old": item["path"], "new": item["new_path"]})
        else:
            failed.append({**item, "reason": "renamed on disk, but Stash doesn't show the scene at the new path yet"})
            history.add("warning", "history", "rename", "Renamed on disk, Stash not updated yet (rescan)",
                        scene_title=item["title"], details={"old": item["path"], "new": item["new_path"]})
    if confirmed or failed:
        history.event("warning" if failed else "info", "rename",
                      f"Quality names fixed: {len(confirmed)} renamed, {len(failed)} failed, {len(skipped)} skipped")
    return {"renamed": confirmed, "failed": failed, "skipped": skipped}
