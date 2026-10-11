"""Existing Stash groups with MORE THAN 3 files (v0.112.0) -- normally a film that is split into scenes. For them: the StashDB scene check
(film_stashdb) and, for scenes without a title, a title derived from the file name (dry run first, backup, undo)."""
import json
import os
import re
import time

import film_stashdb

MIN_FILES = 4  # "more than three"

_GROUPS_QUERY = """
{ findGroups(filter: {per_page: -1}) { groups { id name date duration director synopsis urls scene_count studio { name }
  scenes { id title stash_ids { endpoint stash_id } performers { name } files { path duration } } } } }
"""
_QUALITY = re.compile(r"\b(?:web-?dl|web-?rip|dvd-?rip|hdtv|bluray|blu-ray|bdrip)\b[- ]*\d*p?|\b\d{3,4}p\b|\((?:19|20)\d{2}\)", re.I)


def _entry(g):
    files = [(s, f) for s in g["scenes"] for f in (s.get("files") or [])]
    file_minutes = round(sum((f.get("duration") or 0) for _, f in files) / 60, 1)
    return {
        "id": g["id"], "name": g["name"], "date": g.get("date"), "studio": (g.get("studio") or {}).get("name"),
        "urls": g.get("urls") or [], "director": g.get("director") or "", "synopsis": g.get("synopsis") or "",
        "files": len(files), "scenes": len(g["scenes"]), "file_minutes": file_minutes,
        "group_minutes": round(g["duration"] / 60, 1) if g.get("duration") else None,
        "untitled": sum(1 for s in g["scenes"] if not (s.get("title") or "").strip()),
        "with_provider_id": sum(1 for s in g["scenes"] if s.get("stash_ids")),
        # StashDB ids the group's scenes already carry: those scenes are "owned" in the StashDB check
        "stashdb_ids": sorted({x["stash_id"] for s in g["scenes"] for x in (s.get("stash_ids") or []) if "stashdb.org" in (x.get("endpoint") or "")}),
        "scene_list": [{"id": s["id"], "title": s.get("title") or "", "minutes": round(sum((f.get("duration") or 0) for f in s["files"]) / 60, 1),
                        "file": os.path.basename(s["files"][0]["path"]) if s["files"] else "", "has_id": bool(s.get("stash_ids"))}
                       for s in g["scenes"]],
    }


def list_groups(stash_client):
    out = [e for e in (_entry(g) for g in stash_client._graphql(_GROUPS_QUERY, {})["findGroups"]["groups"]) if e["files"] >= MIN_FILES]
    out.sort(key=lambda r: (-r["files"], r["name"].lower()))
    return out


_GROUP_QUERY = """query($id: ID!) { findGroup(id: $id) { id name date duration director synopsis urls studio { name }
  scenes { id title stash_ids { endpoint stash_id } performers { name } files { path duration } } } }"""


def group_info(stash_client, group_id):
    """ONE group of any size (the Group page tab) in the same shape as list_groups() entries; None when it does not exist."""
    g = stash_client._graphql(_GROUP_QUERY, {"id": str(group_id)})["findGroup"]
    return _entry(g) if g else None


def _series_regex(group_name):
    words = re.findall(r"[A-Za-z0-9]+", group_name or "")
    return re.compile(r"[^A-Za-z0-9]*".join(re.escape(w) for w in words), re.I) if words else None


def derive_title(group_name, file_name):
    """'Adriana Luna - Breakin Em In 14 (Blowbang).avi' in group "Breakin' 'Em In #14" -> 'Adriana Luna (Blowbang)'; a file that is just the
    film's own name -> '<group name> (full length)'."""
    base = os.path.splitext(file_name or "")[0]
    base = _QUALITY.sub(" ", base)
    rx = _series_regex(group_name)
    if rx:
        base = rx.sub(" ", base)
    base = re.sub(r"\(\s*\)", " ", base)
    base = re.sub(r"\s*[-–]\s*(?=\()", " ", base)
    base = re.sub(r"\s+", " ", base).strip(" -–_.")
    return base or f"{group_name} (full length)"


def name_preview(group):
    """What would be set: only scenes WITHOUT a title."""
    items = []
    for s in group["scene_list"]:
        if not s["title"].strip() and s["file"]:
            items.append({"scene_id": s["id"], "file": s["file"], "title": derive_title(group["name"], s["file"])})
    return items


def check_stashdb(client, group):
    """The StashDB scene check for an existing group (ONE request) + a duration pairing of the local scenes with the StashDB scenes found
    (display only -- nothing is linked)."""
    minutes = group["group_minutes"] or group["file_minutes"]
    film = {"title": group["name"], "duration_min": minutes, "release_date": group.get("date")}
    result = film_stashdb.check(client, film)
    owned = set(group.get("stashdb_ids") or [])
    for c in result["scenes"]:
        c["owned"] = c["id"] in owned  # a local scene of this group already carries this StashDB scene's id
    pairs, used = [], set()
    for local in group["scene_list"]:
        best = None
        for c in result["scenes"]:
            if c["id"] in used or not c["minutes"] or not local["minutes"]:
                continue
            gap = abs(c["minutes"] / local["minutes"] - 1)
            if gap <= 0.05 and (best is None or gap < best[0]):
                best = (gap, c)
        if best:
            used.add(best[1]["id"])
            pairs.append({"scene_id": local["id"], "file": local["file"], "local_minutes": local["minutes"],
                          "stashdb_id": best[1]["id"], "stashdb_title": best[1]["title"], "stashdb_minutes": best[1]["minutes"]})
    result["pairs"] = pairs
    result["group_minutes"] = minutes
    return result


def name_apply(stash_client, group, items, data_dir):
    """Sets the titles of scenes that STILL have none. The previous state is written to a backup file first, and the file must exist before
    anything is changed. Returns {"backup", "applied": [{scene_id, previous}]}."""
    valid = {s["id"] for s in group["scene_list"] if not s["title"].strip()}
    todo = [i for i in items if str(i.get("scene_id")) in valid and (i.get("title") or "").strip()]
    if not todo:
        return {"error": "nothing to name (every scene already has a title)"}
    backup = os.path.join(data_dir, f"group_scene_titles_{group['id']}_{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(backup, "w", encoding="utf-8") as fh:
        json.dump({"group": group["id"], "name": group["name"], "scenes": [{"scene_id": str(i["scene_id"]), "previous_title": ""} for i in todo]},
                  fh, ensure_ascii=False, indent=1)
    if not os.path.exists(backup) or os.path.getsize(backup) == 0:
        return {"error": "the backup file could not be written -- nothing was changed"}
    for i in todo:
        stash_client._graphql("mutation($i: SceneUpdateInput!) { sceneUpdate(input: $i) { id } }",
                              {"i": {"id": str(i["scene_id"]), "title": i["title"].strip()}})
    return {"ok": True, "backup": os.path.basename(backup), "applied": [{"scene_id": str(i["scene_id"]), "previous": ""} for i in todo]}


def name_undo(stash_client, applied):
    for a in applied:
        stash_client._graphql("mutation($i: SceneUpdateInput!) { sceneUpdate(input: $i) { id } }",
                              {"i": {"id": str(a["scene_id"]), "title": a.get("previous") or ""}})
    return {"ok": True, "restored": len(applied)}


# ---------- assigning the scenes of a group by hand (v0.114.0) ----------

_SNAPSHOT_QUERY = """query($id: ID!) { findScene(id: $id) { title code date details director urls studio { id }
  performers { id } tags { id } stash_ids { endpoint stash_id } groups { group { id } scene_index } } }"""


def snapshot(stash_client, scene_id):
    sc = stash_client._graphql(_SNAPSHOT_QUERY, {"id": str(scene_id)})["findScene"]
    return {"title": sc.get("title") or "", "code": sc.get("code") or "", "date": sc.get("date"), "details": sc.get("details") or "",
            "director": sc.get("director") or "", "urls": sc.get("urls") or [], "studio_id": (sc.get("studio") or {}).get("id"),
            "performer_ids": [p["id"] for p in sc.get("performers") or []], "tag_ids": [t["id"] for t in sc.get("tags") or []],
            "stash_ids": [{"endpoint": x["endpoint"], "stash_id": x["stash_id"]} for x in sc.get("stash_ids") or []],
            "groups": [{"group_id": g["group"]["id"], "scene_index": g.get("scene_index")} for g in sc.get("groups") or []]}


def restore_snapshot(stash_client, scene_id, snap):
    stash_client._graphql("mutation($i: SceneUpdateInput!) { sceneUpdate(input: $i) { id } }", {"i": {"id": str(scene_id), **snap}})


def assign(stash_client, group, items, data_dir):
    """Per local scene of the group: `stashdb_id` = this scene IS that StashDB scene (its metadata is applied like at an import: title, date,
    studio, performers, StashDB id -- library_check.apply_fix, which refuses an id another local scene already holds), or `title` = just
    a name by hand. The complete previous state of every touched scene goes to a backup file first (checked before anything changes) and is
    returned for the Undo. Returns {"backup", "applied": [{scene_id, snapshot}], "errors": [{scene_id, error}]}."""
    import library_check
    in_group = {s["id"] for s in group["scene_list"]}
    todo = [i for i in items if str(i.get("scene_id")) in in_group
            and (i.get("stashdb_id") or (i.get("title") or "").strip() or i.get("scene_index") is not None)]
    ids = [i["stashdb_id"] for i in todo if i.get("stashdb_id")]
    if not todo:
        return {"error": "nothing to apply"}
    if len(ids) != len(set(ids)):
        return {"error": "the same StashDB scene was picked for two local scenes"}
    snaps = {str(i["scene_id"]): snapshot(stash_client, i["scene_id"]) for i in todo}
    backup = os.path.join(data_dir, f"group_assign_{group['id']}_{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(backup, "w", encoding="utf-8") as fh:
        json.dump({"group": group["id"], "name": group["name"], "scenes": snaps}, fh, ensure_ascii=False, indent=1)
    if not os.path.exists(backup) or os.path.getsize(backup) == 0:
        return {"error": "the backup file could not be written -- nothing was changed"}
    applied, errors = [], []
    for i in todo:
        sid = str(i["scene_id"])
        try:
            if i.get("stashdb_id"):
                library_check.apply_fix(stash_client, sid, i["stashdb_id"], create_missing_performers=True, endpoint=None,
                                        keep_other_endpoints=True)
            elif (i.get("title") or "").strip():
                stash_client._graphql("mutation($i: SceneUpdateInput!) { sceneUpdate(input: $i) { id } }",
                                      {"i": {"id": sid, "title": i["title"].strip()}})
            if i.get("scene_index") is not None:  # the position in THIS group (other group memberships are kept as they are)
                others = [g for g in snaps[sid].get("groups") or [] if str(g["group_id"]) != str(group["id"])]
                stash_client._graphql("mutation($i: SceneUpdateInput!) { sceneUpdate(input: $i) { id } }",
                                      {"i": {"id": sid, "groups": others + [{"group_id": str(group["id"]), "scene_index": int(i["scene_index"])}]}})
            applied.append({"scene_id": sid, "snapshot": snaps[sid]})
        except Exception as e:  # noqa: BLE001
            errors.append({"scene_id": sid, "error": f"{type(e).__name__}: {e}"})
    return {"ok": True, "backup": os.path.basename(backup), "applied": applied, "errors": errors}


def assign_undo(stash_client, applied):
    for a in applied:
        restore_snapshot(stash_client, a["scene_id"], a["snapshot"])
    return {"ok": True, "restored": len(applied)}


# ---------- ThePornDB metadata for an EXISTING group (v0.123.0) ----------

_META_QUERY = """query($id: ID!) { findGroup(id: $id) { id name date duration director synopsis urls front_image_path back_image_path
  studio { id name } } }"""
_META_FIELDS = ("name", "date", "duration", "director", "synopsis", "studio", "urls", "front_image", "back_image")
_LABELS = {"name": "Name", "date": "Date", "duration": "Length", "director": "Director", "synopsis": "Description", "studio": "Studio",
           "urls": "URLs", "front_image": "Front cover", "back_image": "Back cover"}


def _stash_image(stash_client, path):
    """The group's own cover as a data: URL; None when it has none (Stash serves a placeholder with ?default=true) or it can't be read."""
    import base64
    if not path or "default=true" in path:
        return None
    try:
        r = stash_client.session.get(path, timeout=30)
        if r.status_code != 200 or not r.content:
            return None
        kind = r.headers.get("content-type", "image/jpeg").split(";")[0]
        return f"data:{kind};base64,{base64.b64encode(r.content).decode()}"
    except Exception:  # noqa: BLE001
        return None


def _current_group(stash_client, group_id):
    g = stash_client._graphql(_META_QUERY, {"id": str(group_id)})["findGroup"]
    if not g:
        return None
    return {"name": g.get("name") or "", "date": g.get("date") or "", "duration": g.get("duration") or 0, "director": g.get("director") or "",
            "synopsis": g.get("synopsis") or "", "studio_id": (g.get("studio") or {}).get("id"), "studio_name": (g.get("studio") or {}).get("name") or "",
            "urls": list(g.get("urls") or []), "front_path": g.get("front_image_path"), "back_path": g.get("back_image_path")}


def metadata_preview(stash_client, movies, group_id, record):
    """Dry run: per field the group's current value and the ThePornDB value. A field is `checked` by default only when the group has
    nothing there yet and ThePornDB has a value (the name is never pre-checked, a cover is replaced only on the user's tick). No
    ThePornDB request (the stored movie record is used); the cover images come from the CDN. Nothing is changed."""
    cur = _current_group(stash_client, group_id)
    if cur is None:
        return {"error": "that group does not exist (any more)"}
    inp, notes = movies.tpdb_group_input(record, movies.local_studio_index())
    has_front, has_back = bool(cur["front_path"] and "default=true" not in cur["front_path"]), bool(cur["back_path"] and "default=true" not in cur["back_path"])
    new_urls = [u for u in inp.get("urls", []) if u not in cur["urls"]]
    studio_site = (record.get("site") or {}).get("name") or ""

    def row(key, current, new, empty, differs, show_new=None, preview_url=None):
        have_new = new not in (None, "", 0, [])
        return {"key": key, "label": _LABELS[key], "current": current, "new": show_new if show_new is not None else new, "has_new": have_new,
                "differs": bool(have_new and differs), "checked": bool(have_new and empty and key != "name"), "preview_url": preview_url}

    rows = [
        row("name", cur["name"], inp["name"], not cur["name"], cur["name"] != inp["name"]),
        row("date", cur["date"], inp.get("date"), not cur["date"], cur["date"] != inp.get("date")),
        row("duration", f"{round(cur['duration'] / 60)} min" if cur["duration"] else "", inp.get("duration"), not cur["duration"], cur["duration"] != inp.get("duration"),
            show_new=f"{round(inp['duration'] / 60)} min" if inp.get("duration") else ""),
        row("director", cur["director"], inp.get("director"), not cur["director"], cur["director"] != inp.get("director")),
        row("synopsis", cur["synopsis"], inp.get("synopsis"), not cur["synopsis"], cur["synopsis"] != inp.get("synopsis")),
        row("studio", cur["studio_name"], inp.get("studio_id"), not cur["studio_id"], str(cur["studio_id"]) != str(inp.get("studio_id")),
            show_new=studio_site if inp.get("studio_id") else ""),
        row("urls", ", ".join(cur["urls"]), new_urls, not cur["urls"], bool(new_urls), show_new=", ".join(new_urls)),
        row("front_image", "has a cover" if has_front else "", inp.get("front_image"), not has_front, has_front, show_new="ThePornDB cover" if inp.get("front_image") else "",
            preview_url=record.get("poster") if isinstance(record.get("poster"), str) else None),
        row("back_image", "has a back cover" if has_back else "", inp.get("back_image"), not has_back, has_back,
            show_new="ThePornDB back cover" if inp.get("back_image") else "", preview_url=record.get("back_image") if isinstance(record.get("back_image"), str) else None),
    ]
    notes = [n for n in notes if not n.startswith("studio") or not inp.get("studio_id")]
    return {"group_id": str(group_id), "rows": rows, "notes": [n for n in notes if n != "no front cover" or not inp.get("front_image")],
            "images": {"front": inp.get("front_image"), "back": inp.get("back_image")}}


def metadata_apply(stash_client, movies, group_id, record, keys, data_dir):
    """Sets the ticked fields on the existing group (groupUpdate). The previous state -- every field AND both cover images -- goes to a
    backup file first (checked before anything changes) and is returned for the Undo. URLs are added, never replaced."""
    keys = [k for k in keys if k in _META_FIELDS]
    if not keys:
        return {"error": "nothing ticked"}
    cur = _current_group(stash_client, group_id)
    if cur is None:
        return {"error": "that group does not exist (any more)"}
    inp, _ = movies.tpdb_group_input(record, movies.local_studio_index())
    update = {"id": str(group_id)}
    previous = {"id": str(group_id)}
    for k in keys:
        if k == "studio":
            if inp.get("studio_id"):
                update["studio_id"], previous["studio_id"] = inp["studio_id"], cur["studio_id"]
        elif k == "urls":
            add = [u for u in inp.get("urls", []) if u not in cur["urls"]]
            if add:
                update["urls"], previous["urls"] = cur["urls"] + add, cur["urls"]
        elif k in ("front_image", "back_image"):
            if inp.get(k):
                update[k] = inp[k]
                previous[k] = _stash_image(stash_client, cur["front_path" if k == "front_image" else "back_path"])  # None = it had none
        elif inp.get(k) not in (None, "", 0):
            update[k], previous[k] = inp[k], cur[k]
    if len(update) == 1:
        return {"error": "ThePornDB has no value for the ticked fields"}
    backup = os.path.join(data_dir, f"group_metadata_{group_id}_{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(backup, "w", encoding="utf-8") as fh:
        json.dump({"group": str(group_id), "name": cur["name"], "previous": previous, "fields": sorted(update)}, fh, ensure_ascii=False, indent=1)
    if not os.path.exists(backup) or os.path.getsize(backup) == 0:
        return {"error": "the backup file could not be written -- nothing was changed"}
    stash_client._graphql("mutation($i: GroupUpdateInput!) { groupUpdate(input: $i) { id } }", {"i": update})
    return {"ok": True, "backup": os.path.basename(backup), "applied": {"previous": previous, "fields": sorted(k for k in update if k != "id")}}


def metadata_undo(stash_client, applied):
    """Puts the previous values back. A cover the group did not have is cleared again (an empty string removes it in Stash)."""
    prev = dict(applied["previous"])
    for k in ("front_image", "back_image"):
        if k in prev and prev[k] is None:
            prev[k] = ""
    stash_client._graphql("mutation($i: GroupUpdateInput!) { groupUpdate(input: $i) { id } }", {"i": prev})
    return {"ok": True, "restored": sorted(k for k in prev if k != "id")}


# ---------- delete a group (v0.168.0: "Dismiss & delete group" on a movie that came from an empty group) ----------

_DELETE_QUERY = """query($id: ID!) { findGroup(id: $id) { id name scenes { id title files { path } } } }"""


def delete_check(stash_client, group_id):
    """What deleting this group would touch, from Stash right now: {exists, name, scenes, files, file_list (first 10 paths)}."""
    g = stash_client._graphql(_DELETE_QUERY, {"id": str(group_id)})["findGroup"]
    if not g:
        return {"exists": False}
    paths = [f["path"] for s in g["scenes"] for f in (s.get("files") or [])]
    return {"exists": True, "name": g["name"], "scenes": len(g["scenes"]), "files": len(paths), "file_list": paths[:10]}


def delete_group(stash_client, group_id, data_dir, force=False):
    """Deletes the GROUP in Stash (groupDestroy: the scenes and their files stay, they are only no longer in this group). A group that holds
    files is only deleted with force=True (the caller asked the user first). Its fields and both covers go to a backup file first; if that
    file cannot be written nothing is deleted. There is no restore function (the user decided so): the file is the safety net."""
    check = delete_check(stash_client, group_id)
    if not check["exists"]:
        return {"error": "that group does not exist (any more)"}
    if check["files"] and not force:
        return {"error": "the group holds files", "needs_force": True, **check}
    cur = _current_group(stash_client, group_id)
    backup = os.path.join(data_dir, f"group_deleted_{group_id}_{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(backup, "w", encoding="utf-8") as fh:
        json.dump({"group": str(group_id), "name": cur["name"], "fields": {k: cur[k] for k in ("date", "duration", "director", "synopsis", "studio_name", "urls")},
                   "front_image": _stash_image(stash_client, cur["front_path"]), "back_image": _stash_image(stash_client, cur["back_path"]),
                   "scenes": check["scenes"], "files": check["files"], "file_list": check["file_list"]}, fh, ensure_ascii=False, indent=1)
    if not os.path.exists(backup) or os.path.getsize(backup) == 0:
        return {"error": "the backup file could not be written -- nothing was deleted"}
    stash_client._graphql("mutation($i: GroupDestroyInput!) { groupDestroy(input: $i) }", {"i": {"id": str(group_id)}})
    return {"ok": True, "backup": os.path.basename(backup), **check}
