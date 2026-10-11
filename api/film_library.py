"""Stage D of Whisparr for movies (v0.160.0): a wanted movie whose file is ALREADY in the library.

Whisparr imported many movies long before this wishlist existed, often under an older id (or a duplicate ThePornDB id). The wishlist would download
them again. This finds them: for a movie of the wishlist (status 'wanted'), the Whisparr entry (its own, or the same title + year under another id)
that HAS a file -> the Stash scene at that path -> its group. The verdict:
- clear:  the file's length fits the movie's ThePornDB length (+-15 %, the film import's own tolerance) and the scene is in at most one group.
- manual: the length does not fit (e.g. ThePornDB lists one disc only) or the scene is in several groups -- the user looks and decides.
Using the existing file is `link_group` (the scene is already in a group: the movie becomes 'imported' with that group, nothing else changes) or
`make_group` (the scene has no group: film_import.finalize builds it -- into the movie's own empty Stash group if it has one -- with its usual length
check). Nothing is downloaded, moved or deleted. "keep" leaves the movie on the wishlist (searched as before)."""
import json
import logging

import film_import
import film_repo
import film_whisparr
import history

logger = logging.getLogger(__name__)


def _entries(client, film):
    """Whisparr entries that may hold this movie's file: its own entry and the same movie under another id (title + year)."""
    found, seen = [], set()
    own = client.movie_cached(film["whisparr_id"]) if film.get("whisparr_id") else None
    for m in ([own] if own else []) + client.movies_by_title_year(film["title"], film.get("year")):
        if m and m["id"] not in seen:
            seen.add(m["id"])
            found.append(m)
    return found


def evaluate(client, stash, film):
    """None when nothing is known about a file of this movie, else the verdict dict (see the module docstring)."""
    entry = next((m for m in _entries(client, film) if (m.get("movieFile") or {}).get("path")), None)
    if entry is None:
        return None
    path = entry["movieFile"]["path"]
    out = {"film_id": film["id"], "title": film["title"], "year": film.get("year"), "whisparr_entry": entry["id"], "path": path, "expected_min": film.get("duration_min")}
    scene = stash.find_scene_by_path(path)
    if scene is None:
        return {**out, "verdict": "not_in_stash"}
    detail = stash._graphql("query($id: ID!) { findScene(id: $id) { id files { path width height duration size } groups { group { id name } } } }",
                            {"id": str(scene["id"])})["findScene"]
    file = next((f for f in detail["files"] if f["path"] == path), detail["files"][0])
    minutes = (file.get("duration") or 0) / 60
    expected = film.get("duration_min")
    length_ok = bool(expected) and abs(minutes / expected - 1) <= film_import.LENGTH_TOLERANCE
    groups = [g["group"] for g in detail["groups"]]
    own_group = str(film["stash_group_id"]) if film.get("stash_group_id") else None
    conflict_group = bool(own_group and groups and all(str(g["id"]) != own_group for g in groups))  # the movie has its own group, the scene sits in another one
    return {**out, "scene_id": str(detail["id"]), "resolution": f"{file.get('width')}x{file.get('height')}", "size_mb": round((file.get("size") or 0) / 1e6),
            "minutes": round(minutes, 1), "length_ok": length_ok, "groups": [{"id": str(g["id"]), "name": g["name"]} for g in groups],
            "action": "link_group" if groups else "make_group",
            "verdict": "clear" if length_ok and len(groups) <= 1 and not conflict_group else "manual"}


def check(db, client, stash):
    """Dry run: {"clear": [...], "manual": [...], "other": n} over the wanted movies that were not set aside ("keep")."""
    out = {"clear": [], "manual": [], "other": 0}
    for film in film_repo.list_films(db, ["wanted"], per_page=0)["rows"]:
        if film.get("library_choice") == "keep" or (film.get("mode") or "file") != "file":
            continue
        try:
            v = evaluate(client, stash, film)
        except Exception:  # noqa: BLE001
            logger.exception("library check: %s failed", film["title"])
            v = None
        if v is None or v["verdict"] == "not_in_stash":
            out["other"] += 1
        else:
            out[v["verdict"]].append(v)
    return out


def apply_one(db, stash, movies, get_provider_client, film, verdict, force=False):
    """Uses the existing file for this movie. Returns {"id", "title", "status"} (status: imported / import_failed / waiting) or {"error"}."""
    film = film_repo.get(db, film["id"], with_json=True)
    if film["status"] != "wanted":
        return {"id": film["id"], "title": film["title"], "error": "the movie is not on the wishlist any more"}
    if verdict["action"] == "link_group":
        group = verdict["groups"][0]
        film_repo.update(db, film["id"], status="imported", imported_group_id=group["id"], stash_group_id=group["id"], import_error=None, pending_import_path=None,
                         whisparr_file_state="synced", whisparr_file_checked_at=film_repo.now())
        history.event("info", "movies", f"{film['title']}: already in the library -- linked to the existing group {group['id']} ({group['name']})",
                      details={"scene": verdict["scene_id"], "file": verdict["path"]})
        return {"id": film["id"], "title": film["title"], "status": "imported", "group_id": group["id"]}
    film_repo.update(db, film["id"], status="grabbed", pending_import_path=json.dumps([verdict["path"]]), import_error=None,
                     whisparr_file_state="synced", whisparr_file_checked_at=film_repo.now())
    out = film_import.finalize(db, stash, film_repo.get(db, film["id"], with_json=True), movies, get_provider_client, force=force)
    if out["status"] == "imported":
        history.event("info", "movies", f"{film['title']}: already in the library -- its file was put into the group", details={"scene": verdict["scene_id"], "file": verdict["path"]})
    return {"id": film["id"], "title": film["title"], "status": out["status"]}


def keep(db, film_id):
    """The user wants this movie searched although a file exists (e.g. 480p is not enough): it leaves the list."""
    film_repo.update(db, film_id, library_choice="keep")
    return {"ok": True}
