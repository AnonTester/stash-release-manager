"""Film wishlist (v0.107.0) -- see film_repo.py / film_match.py. Films/groups the user wants, separate from the scene releases."""
import os

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

import film_feed
import film_match
import film_search
import film_repo
import history
import film_groups
import film_import
import film_series
import film_stashdb
import indexer_search
import release_repo

router = APIRouter(prefix="/films", tags=["films"])

_db = None
_get_provider_client = None
_data_dir = "/data"
_get_movies = None
_seed_job = None
_get_stash_client = None
_get_film_source = None
_file_tools = None  # (recycle(stash_path), to_local(stash_path)) from main.py
_run_feed = None  # main.py's run_film_feed (the manual "Run scan now" of the Movies part)
_library = None  # main.py's "already in the library" tools (stage D): {check(), apply(ids, only_clear, force), keep(id)}
_whisparr = None  # main.py's Whisparr bookkeeping of the movies: {kick(), start(dry_run), status()}

def _kick_whisparr(film_ids=()):
    """Movies went onto the wishlist: tell Whisparr about them in the background (per preset, see film_whisparr.py). Never blocks or fails the request."""
    try:
        if _whisparr:
            _whisparr["kick"](list(film_ids))
    except Exception:  # noqa: BLE001
        pass


STATUSES = ("suggested", "wanted", "grabbed", "imported", "import_failed", "dismissed")


def init_films_router(db, get_provider_client, data_dir, get_movies, seed_job, get_stash_client=None, get_film_source=None, file_tools=None, whisparr=None, run_feed=None, library=None):
    global _file_tools, _whisparr, _run_feed, _library
    _library = library
    _run_feed = run_feed
    _file_tools = file_tools
    _whisparr = whisparr
    global _db, _get_provider_client, _data_dir, _get_movies, _seed_job, _get_stash_client, _get_film_source
    _get_film_source = get_film_source
    _db, _get_provider_client, _data_dir, _get_movies, _seed_job = db, get_provider_client, data_dir, get_movies, seed_job
    _get_stash_client = get_stash_client


def _movie_fields(rec):
    """film_want columns from a ThePornDB /movies record."""
    return film_feed.movie_fields(rec)


@router.get("")
def list_films(statuses: str = "", q: str = "", page: int = 1, per_page: int = 40, order: str = "recent"):
    wanted = [s for s in statuses.split(",") if s in STATUSES]
    out = film_repo.list_films(_db, wanted or None, q or None, page, per_page, order)
    for f in out["rows"]:  # mode B: how far are the scenes
        if f.get("mode") == "scenes" and f.get("scene_ids"):
            have = 0
            for sc in f["scene_ids"]:
                have += any(r["status"] == "imported" for r in release_repo.list_rows_for_scene(_db, "stashdb", sc["id"]))
            f["scenes_imported"], f["scenes_total"] = have, len(f["scene_ids"])
    out["counts"] = film_repo.counts(_db)
    return out


@router.get("/activity")
def activity():
    """What the Activity page lists for movies (v0.126.0): every 'grabbed' and 'import_failed' movie. The live progress of the one-file movies
    comes with /releases/download-progress under the key "film:<id>"."""
    rows = film_repo.list_films(_db, ["grabbed", "import_failed"], per_page=0)["rows"]
    # wanted movies the automatic search has something to say about: a candidate to decide on (Needs review) or "nothing usable" (Stuck)
    rows += [f for f in film_repo.list_films(_db, ["wanted"], per_page=0)["rows"]
             if f.get("review_candidate") or f.get("last_search_result") in ("no_match", "rejected", "error")]
    out = []
    for f in rows:
        mode = f.get("mode") or "file"
        item = {"id": f["id"], "title": f["title"], "year": f.get("year"), "status": f["status"], "mode": mode,
                "import_error": f.get("import_error"), "stash_group_id": f.get("stash_group_id"), "key": f"film:{f['id']}",
                "image_url": f.get("image_url"), "studio_name": f.get("studio_name"), "release_date": f.get("release_date"),
                "review_candidate": f.get("review_candidate"), "last_search_at": f.get("last_search_at"),
                "last_search_result": f.get("last_search_result")}
        if mode == "scenes":
            have = sum(1 for sc in (f.get("scene_ids") or [])
                       if any(r["status"] == "imported" for r in release_repo.list_rows_for_scene(_db, "stashdb", sc["id"])))
            item["scenes_imported"], item["scenes_total"] = have, len(f.get("scene_ids") or [])
        item["has_files"] = bool(f.get("pending_import_path"))  # files of an import that is not finished: the "Wrong movie" buttons
        if f.get("pending_import_path"):  # the files of the movie as Stash knows them (to open and check them), v0.126.1
            item["scenes"] = _film_scenes(f["pending_import_path"])
        out.append(item)
    return {"rows": out}


@router.get("/imported")
def imported():
    """The imported movies for History > Imported (v0.128.2): newest first, with the Group the import built."""
    rows = film_repo.list_films(_db, ["imported"], per_page=0)["rows"]
    rows.sort(key=lambda f: f.get("updated_at") or "", reverse=True)
    return {"rows": [{"id": f["id"], "title": f["title"], "year": f.get("year"), "status": "imported", "mode": f.get("mode") or "file",
                      "image_url": f.get("image_url"), "studio_name": f.get("studio_name"), "release_date": f.get("release_date"),
                      "stash_group_id": f.get("imported_group_id") or f.get("stash_group_id"),
                      "imported_at": f.get("updated_at"), "key": f"film:{f['id']}"} for f in rows]}


def _film_scenes(pending):
    """[{scene_id, file, minutes, title, groups}] for the files of a movie (JSON list of Stash paths); a file Stash has no scene for yet
    is listed without scene_id."""
    import json as _json
    try:
        paths = _json.loads(pending) if isinstance(pending, str) else list(pending or [])
    except ValueError:
        return []
    stash = _get_stash_client()
    out = []
    for path in paths:
        entry = {"scene_id": None, "file": os.path.basename(path), "minutes": None, "title": "", "groups": []}
        try:
            sc = stash.find_scene_by_path(path)
            if sc:
                d = stash._graphql("query($i: ID!) { findScene(id: $i) { id title groups { group { id name } } files { path duration } } }", {"i": str(sc["id"])})["findScene"]
                fl = next((x for x in d["files"] if x["path"] == path), d["files"][0] if d["files"] else None)
                entry.update(scene_id=d["id"], title=d.get("title") or "", minutes=round((fl or {}).get("duration", 0) / 60, 1) if fl else None,
                             groups=[{"id": g["group"]["id"], "name": g["group"]["name"]} for g in d.get("groups") or []])
        except Exception:  # noqa: BLE001
            pass
        out.append(entry)
    return out


@router.get("/search-tpdb")
def search_tpdb(q: str, limit: int = 12):
    """ThePornDB movie search (ONE request) for 'Add to film wishlist' / 'Link movie'."""
    if not q.strip():
        raise HTTPException(400, "q is required")
    client = _get_provider_client("theporndb")
    if client is None:
        raise HTTPException(503, "ThePornDB is not configured")
    try:
        resp = client._get("/movies", params={"q": q.strip(), "per_page": max(1, min(limit, 50))})
        resp.raise_for_status()
        results = resp.json().get("data") or []
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"ThePornDB search failed: {e}")
    rows = []
    for rec in results:
        f = _movie_fields(rec)
        rows.append({**{k: v for k, v in f.items() if k != "tpdb_json"}, "record": rec,
                     "on_list": film_repo.find_duplicate(_db, rec.get("id"), None, None) is not None})
    return {"results": rows}


class AddBody(BaseModel):
    movie: dict | None = None
    title: str | None = None
    year: int | None = None


@router.post("/add")
def add_film(body: AddBody):
    """Puts a film on the wishlist: from a ThePornDB search result (`movie`), or by hand (`title` [+ `year`], link a movie later)."""
    if body.movie:
        fields = _movie_fields(body.movie)
        source = "theporndb-search"
    elif body.title and body.title.strip():
        fields = {"title": body.title.strip(), "year": body.year}
        source = "manual"
    else:
        raise HTTPException(400, "movie or title is required")
    film_id, created = film_repo.add(_db, **fields, source=source, status="wanted")
    _kick_whisparr([film_id])
    if created:
        history.event("info", "movies", f"Movie on the wishlist: {fields['title']}")
    return {"ok": True, "id": film_id, "created": created, "film": film_repo.get(_db, film_id)}


class StatusBody(BaseModel):
    status: str


@router.post("/{film_id}/status")
def set_status(film_id: int, body: StatusBody):
    """Accept a suggestion / dismiss / restore. Returns the previous status for the Undo."""
    if body.status not in ("wanted", "dismissed", "suggested"):
        raise HTTPException(400, "status must be wanted, dismissed or suggested")
    film = film_repo.get(_db, film_id)
    if not film:
        raise HTTPException(404, "movie not found")
    film_repo.update(_db, film_id, status=body.status)
    if body.status == "wanted":
        _kick_whisparr([film_id])
    return {"ok": True, "previous": film["status"]}


class DismissDeleteBody(BaseModel):
    dry_run: bool = True
    force: bool = False


@router.post("/{film_id}/dismiss-delete-group")
def dismiss_delete_group(film_id: int, body: DismissDeleteBody):
    """"Dismiss & delete group" (v0.168.0): only for a movie of the wishlist that came from an EMPTY Stash group. `dry_run` (default) only says
    what the group holds now. The group is deleted first (a group that holds files only with `force`); only if that worked is the movie
    dismissed. Scenes and files are never deleted, only the group. Returns the previous status for the Undo (which does not bring the group back)."""
    film = film_repo.get(_db, film_id)
    if not film:
        raise HTTPException(404, "movie not found")
    if film["source"] != "empty-group" or not film.get("stash_group_id") or film["status"] not in ("suggested", "wanted"):
        raise HTTPException(400, "this movie did not come from an empty group (or is not on the wishlist any more)")
    stash = _get_stash_client()
    if body.dry_run:
        return {"dry_run": True, **film_groups.delete_check(stash, film["stash_group_id"])}
    res = film_groups.delete_group(stash, film["stash_group_id"], _data_dir, force=body.force)
    if res.get("error"):
        return res
    film_repo.update(_db, film_id, status="dismissed")
    history.event("info", "movies", f"{film['title']}: dismissed and the empty Stash group {film['stash_group_id']} deleted",
                  details={"backup": res["backup"], "scenes": res["scenes"], "files": res["files"]})
    return {**res, "previous": film["status"]}


class LinkBody(BaseModel):
    movie: dict


@router.post("/{film_id}/link")
def link_movie(film_id: int, body: LinkBody):
    """Sets the ThePornDB movie of a film (title, year, studio, length, cover are taken from it)."""
    film = film_repo.get(_db, film_id)
    if not film:
        raise HTTPException(404, "movie not found")
    fields = _movie_fields(body.movie)
    other = film_repo.find_duplicate(_db, fields["tpdb_id"], None, None)
    if other and other != film_id:
        raise HTTPException(409, "this ThePornDB movie is already on the movie wishlist")
    film_repo.update(_db, film_id, **fields, match_class="linked")
    return {"ok": True, "film": film_repo.get(_db, film_id)}


_GRAB_MESSAGES = {
    indexer_search.RESULT_NO_DOWNLOAD_CLIENT: "no download client is configured for this release's protocol (Settings > Download clients)",
    indexer_search.RESULT_CLIENT_NOT_IMPLEMENTED: "this download client type is not supported yet",
    indexer_search.RESULT_DUPLICATE_DOWNLOAD: "the download client already has this release (queue or history)",
    indexer_search.RESULT_DAILY_CAP: "the daily grab cap is reached (setting max_grabs_per_day)",
    indexer_search.RESULT_GRAB_FAILED: "the download client accepted the request but returned no job",
}


def _grab_film(film, candidate):
    """Sends a candidate to the download client and records the job on the movie (used by the manual grab and by Accept of a found candidate)."""
    film_id = film["id"]
    if film["status"] not in ("wanted", "import_failed"):
        raise HTTPException(400, f"the movie is '{film['status']}' -- only a movie on the wishlist can be grabbed")
    if not indexer_search.candidate_url(candidate):
        raise HTTPException(400, "candidate.downloadUrl is required")
    sent = indexer_search.send_to_download_client(
        _db, candidate, lambda msg: history.event("warning", "movies", f"{film['title']}: {msg}", details={"release": candidate.get("title")}))
    if sent["result"] != indexer_search.RESULT_GRABBED:
        raise HTTPException(400, _GRAB_MESSAGES.get(sent["result"], sent["result"]))
    film_repo.update(_db, film_id, status="grabbed", download_client_id=sent["client_row"]["id"], download_job_id=sent["nzo_id"],
                     grabbed_title=candidate.get("title"), grabbed_at=film_repo.now(), last_search_result="grabbed", import_error=None,
                     review_candidate=None)
    history.event("info", "movies", f"Grabbed for the movie {film['title']}: {candidate.get('title')}",
                  details={"indexer": candidate.get("indexer"), "job": sent["nzo_id"],
                           **({"size_gb": round(candidate["size"] / 1e9, 2)} if candidate.get("size") else {})})
    return {"ok": True, "grab": {"title": candidate.get("title"), "nzo_id": sent["nzo_id"]}}


@router.post("/{film_id}/grab")
def grab(film_id: int, body: dict):
    """Grabs a result the user picked from the manual search (releases/.../manual-search with the film's own query) for a WANTED film:
    sends it to the download client and records the job on the film. The import comes with the download-complete handling."""
    film = film_repo.get(_db, film_id)
    if not film:
        raise HTTPException(404, "movie not found")
    return _grab_film(film, body.get("candidate") or {})


@router.post("/{film_id}/search")
def search_now(film_id: int):
    """Searches the indexers for this movie NOW (1-3 queries, see film_search.py); the best plausible result becomes the review candidate
    (Activity > Needs review). Nothing is grabbed."""
    film = film_repo.get(_db, film_id)
    if not film:
        raise HTTPException(404, "movie not found")
    if film["status"] != "wanted":
        raise HTTPException(400, f"the movie is '{film['status']}' -- only a movie on the wishlist is searched")
    source = _get_film_source() if _get_film_source else None
    if source is None:
        raise HTTPException(400, "No Prowlarr or direct indexers configured (Settings > Connections / Indexers)")
    out = film_search.search_film(_db, film, source)
    out["judged"] = out.get("judged", [])[:15]
    return out


@router.post("/{film_id}/review-accept")
def review_accept(film_id: int):
    film = film_repo.get(_db, film_id)
    if not film or not film.get("review_candidate"):
        raise HTTPException(404, "no candidate waiting for this movie")
    return _grab_film(film, film["review_candidate"])


@router.post("/{film_id}/review-reject")
def review_reject(film_id: int):
    """Never this release again for this movie; the next search (after the usual wait) looks for another one. Returns what the Undo needs."""
    film = film_repo.get(_db, film_id)
    if not film or not film.get("review_candidate"):
        raise HTTPException(404, "no candidate waiting for this movie")
    cand = film["review_candidate"]
    film_repo.update(_db, film_id, review_candidate=None, last_search_result="rejected",
                     rejected_titles=[*film.get("rejected_titles", []), cand.get("title")])
    history.event("info", "movies", f"{film['title']}: rejected {cand.get('title')}")
    return {"ok": True, "previous": cand}


@router.post("/{film_id}/review-restore")
def review_restore(film_id: int, body: dict):
    """Undo of a reject: the candidate is back and its name is off the rejected list."""
    film = film_repo.get(_db, film_id)
    cand = (body or {}).get("candidate") or {}
    if not film or not cand.get("title"):
        raise HTTPException(404, "movie or candidate not found")
    film_repo.update(_db, film_id, review_candidate=cand, last_search_result="needs_review",
                     rejected_titles=[t for t in film.get("rejected_titles", []) if t != cand.get("title")])
    return {"ok": True}


@router.post("/{film_id}/check-stashdb")
def check_stashdb(film_id: int):
    """Is the film on StashDB as scenes? ONE StashDB request, only on click. The result is stored on the film."""
    film = film_repo.get(_db, film_id)
    if not film:
        raise HTTPException(404, "movie not found")
    client = _get_provider_client("stashdb")
    if client is None:
        raise HTTPException(503, "StashDB is not configured")
    try:
        out = film_stashdb.check(client, film)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"StashDB check failed: {e}")
    full = out.pop("_scenes", {})
    for c in out["scenes"]:  # what the scene wishlist needs later
        s = full.get(c["id"]) or {}
        c.update({"created": s.get("created"), "details": (s.get("details") or "")[:2000], "studio_id": (s.get("studio") or {}).get("id"),
                  "image_url": ((s.get("images") or [{}])[0] or {}).get("url")})
    film_repo.update(_db, film_id, stashdb_json=out)
    history.event("info", "movies", f"StashDB scene check for {film['title']}: {out['message']}")
    return out


@router.post("/{film_id}/use-scenes")
def use_scenes(film_id: int):
    """Mode B: put the film together from its StashDB scenes -- they go on the scene wishlist; when all are imported the Group is built."""
    film = film_repo.get(_db, film_id)
    if not film:
        raise HTTPException(404, "movie not found")
    check = film.get("stashdb")
    if not check or not check.get("offer"):
        raise HTTPException(400, "run 'Check StashDB scenes' first -- the scenes must cover at least 80 % of the movie")
    if film["status"] not in ("suggested", "wanted"):
        raise HTTPException(400, f"the movie is '{film['status']}'")
    added = film_stashdb.put_on_wishlist(_db, film, check["scenes"])
    film_repo.update(_db, film_id, mode="scenes", status="grabbed",
                     scene_ids=[{"id": c["id"], "k": c["k"]} for c in check["scenes"]],
                     grabbed_title=f"{len(check['scenes'])} StashDB scene(s) on the scene wishlist", import_error=None)
    history.event("info", "movies", f"{film['title']}: {len(check['scenes'])} StashDB scene(s) put on the scene wishlist ({len(added)} new)")
    return {"ok": True, "scenes": len(check["scenes"]), "new": len(added)}


class FinalizeBody(BaseModel):
    force: bool = False


@router.post("/{film_id}/finalize")
def finalize_film(film_id: int, body: FinalizeBody):
    """"Group anyway" for a film whose files are in the library but whose length did not fit the ThePornDB movie (status import_failed):
    builds the Group regardless. Also retries a film that waits for its scene(s)."""
    film = film_repo.get(_db, film_id, with_json=True)
    if not film or not film.get("pending_import_path"):
        raise HTTPException(404, "no imported file is recorded for this movie")
    if film["status"] not in ("grabbed", "import_failed"):
        raise HTTPException(400, f"the movie is '{film['status']}'")
    try:
        result = film_import.finalize(_db, _get_stash_client(), film, _get_movies(), _get_provider_client, force=body.force)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    return result


class DiscardBody(BaseModel):
    dismiss: bool = False


@router.post("/{film_id}/discard")
def discard_film(film_id: int, body: DiscardBody):
    """"Wrong movie" for an import_failed movie: the downloaded file(s) go to the recycle bin and their Stash scene(s) are removed, the release is
    blocklisted for this movie; the movie is searched again, or with `dismiss` dismissed."""
    film = film_repo.get(_db, film_id)
    if not film or film["status"] != "import_failed" or not film.get("pending_import_path"):
        raise HTTPException(400, "only a movie whose import failed (with its file in the library) can be discarded")
    if _file_tools is None:
        raise HTTPException(503, "file tools are not available")
    try:
        return film_import.discard(_db, _get_stash_client(), film, _file_tools[0], _file_tools[1], dismiss=body.dismiss)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"{type(e).__name__}: {e}")


class WhisparrSyncBody(BaseModel):
    dry_run: bool = True


@router.post("/feed/run")
def feed_run():
    """Manual "Run scan now" for the movies: one pass of the ThePornDB movie feed (ONE request) -> new movies as suggestions."""
    if not _run_feed:
        raise HTTPException(503, "The movie feed is not available")
    return _run_feed()


@router.post("/whisparr/sync")
def whisparr_sync(body: WhisparrSyncBody):
    """Registers / links the movies of the wishlist in Whisparr per the Whisparr-scope preset (film_whisparr.py), in the background. `dry_run` (default)
    only reports what it would do. Poll /films/whisparr/status."""
    if not _whisparr:
        raise HTTPException(503, "Whisparr bookkeeping is not available")
    result = _whisparr["start"](body.dry_run)
    if result.get("error"):
        raise HTTPException(409, result["error"])
    return result


@router.get("/whisparr/status")
def whisparr_status():
    return _whisparr["status"]() if _whisparr else {"running": False}


@router.post("/whisparr/files/sync")
def whisparr_files_sync(body: WhisparrSyncBody):
    """Stage B: tells Whisparr the folder of the IMPORTED movies and lets it take the file over (film_whisparr.sync_files), in the background.
    `dry_run` (default) only reports what it would do. Poll /films/whisparr/files/status."""
    if not _whisparr or "start_files" not in _whisparr:
        raise HTTPException(503, "Whisparr bookkeeping is not available")
    result = _whisparr["start_files"](body.dry_run)
    if result.get("error"):
        raise HTTPException(409, result["error"])
    return result


@router.post("/whisparr/history/check")
def whisparr_history_check(body: WhisparrSyncBody):
    """Stage C: takes over what Whisparr grabbed / imported for the movies (one history request), in the background. `dry_run` (default) only reports
    which movies would be taken. Poll /films/whisparr/history/status."""
    if not _whisparr or "start_history" not in _whisparr:
        raise HTTPException(503, "Whisparr bookkeeping is not available")
    result = _whisparr["start_history"](body.dry_run)
    if result.get("error"):
        raise HTTPException(409, result["error"])
    return result


@router.get("/whisparr/history/status")
def whisparr_history_status():
    return _whisparr["history_status"]() if _whisparr and "history_status" in _whisparr else {"running": False}


class LibraryApplyBody(BaseModel):
    film_ids: list[int] | None = None  # None = every clear case
    force: bool = False  # only with film_ids: the user looked at a length mismatch


@router.get("/library")
def library_list():
    """Wanted movies whose file is already in the library: {clear, manual, other}. Reads Whisparr's movie list once (cached) and Stash; writes nothing."""
    if not _library:
        raise HTTPException(503, "The library check is not available")
    result = _library["check"]()
    if result.get("error"):
        raise HTTPException(409, result["error"])
    return result


@router.post("/library/apply")
def library_apply(body: LibraryApplyBody):
    if not _library:
        raise HTTPException(503, "The library check is not available")
    result = _library["apply"](body.film_ids, body.film_ids is None, body.force)
    if result.get("error"):
        raise HTTPException(409, result["error"])
    return result


@router.post("/{film_id}/library/keep")
def library_keep(film_id: int):
    if not _library:
        raise HTTPException(503, "The library check is not available")
    return _library["keep"](film_id)


@router.get("/whisparr/files/status")
def whisparr_files_status():
    return _whisparr["files_status"]() if _whisparr and "files_status" in _whisparr else {"running": False}


# ---------- existing Stash groups with more than 3 files (v0.112.0) ----------

_group_checks = {}  # group id -> (time, check result with what the scene wishlist needs); the "put on the wishlist" step uses it


def _group(group_id):
    g = film_groups.group_info(_get_stash_client(), group_id)
    if not g:
        raise HTTPException(404, "that group does not exist (any more)")
    return g


def _enrich(result):
    """Adds what the scene wishlist needs to each scene of a StashDB check, then drops the raw records."""
    full = result.pop("_scenes", {})
    for c in result["scenes"]:
        s = full.get(c["id"]) or {}
        c.update({"created": s.get("created"), "details": (s.get("details") or "")[:2000], "studio_id": (s.get("studio") or {}).get("id"),
                  "image_url": ((s.get("images") or [{}])[0] or {}).get("url")})
    return result


def _group_film(group_id, info):
    """The film row of this group (created when there is none): the Movies wishlist entry that its search / scenes / import hang on."""
    row = film_repo.find_by_group(_db, group_id)
    if row:
        return row
    date = info.get("date") or ""
    film_id, _ = film_repo.add(_db, title=info["name"], year=int(date[:4]) if date[:4].isdigit() else None, release_date=date or None,
                               duration_min=info.get("group_minutes") or info.get("file_minutes") or None, stash_group_id=str(group_id),
                               source="group-page", status="wanted")
    row = film_repo.get(_db, film_id)
    if not row.get("stash_group_id"):  # add() returned an existing row of the same title
        film_repo.update(_db, film_id, stash_group_id=str(group_id))
        row = film_repo.get(_db, film_id)
    if row["status"] == "suggested":
        film_repo.update(_db, film_id, status="wanted")
        row = film_repo.get(_db, film_id)
        _kick_whisparr([film_id])
    return row


@router.get("/group/{group_id}")
def group_page_info(group_id: str):
    """For the tab on a Stash group page: the group (any size) and the Movies wishlist row that belongs to it, if there is one."""
    return {"group": _group(group_id), "movie": film_repo.find_by_group(_db, group_id)}


_group_series = {}  # group id -> (time, {tpdb id -> record}); "Add" uses the lookup of this page visit


def _local_groups():
    return film_feed.stash_groups(_get_stash_client())


@router.get("/group/{group_id}/series")
def group_series(group_id: str):
    """All movies of this group's series on ThePornDB (ONE request), each marked library / wishlist / available."""
    import time as _time
    info = _group(group_id)
    client = _get_provider_client("theporndb")
    if client is None:
        raise HTTPException(503, "ThePornDB is not configured")
    try:
        found = film_series.lookup(client, info["name"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"ThePornDB lookup failed: {e}")
    _group_series[str(group_id)] = (_time.time(), {r["id"]: r for r in found["records"]})
    items = film_series.describe(_db, found["records"], _local_groups())
    return {"series": found["text"], "items": items, "more": found["more"]}


class SeriesAddBody(BaseModel):
    tpdb_ids: list[str]


@router.post("/group/{group_id}/series-add")
def group_series_add(group_id: str, body: SeriesAddBody):
    """Puts the chosen movies of the series on the Movies wishlist (from the lookup of this page visit). Returns the new row ids for the Undo."""
    import time as _time
    cached = _group_series.get(str(group_id))
    if not cached or _time.time() - cached[0] > 3600:
        raise HTTPException(400, "load the series first")
    added, existing = [], 0
    empty = {film_stashdb.series_key(g["name"]): g for g in _local_groups() if g["files"] == 0}  # an empty Stash group is used at the import
    for tid in body.tpdb_ids:
        rec = cached[1].get(tid)
        if not rec:
            continue
        film_id, created = film_repo.add(_db, **_movie_fields(rec), source="series", source_ref=f"series-of-group:{group_id}", status="wanted")
        if created:
            _kick_whisparr([film_id])
        if created:
            added.append(film_id)
            group = empty.get(film_stashdb.series_key(rec.get("title")))
            if group:
                film_repo.update(_db, film_id, stash_group_id=str(group["id"]))
        else:
            existing += 1
    if added:
        history.event("info", "movies", f"{len(added)} movie(s) of the series of group {group_id} put on the Movies wishlist")
    return {"ok": True, "added": added, "existing": existing}


class RemoveBody(BaseModel):
    ids: list[int]


@router.post("/remove-new")
def remove_new(body: RemoveBody):
    """Undo of an add: removes rows that are still untouched (wanted, nothing grabbed)."""
    return {"ok": True, "removed": film_repo.delete_untouched(_db, body.ids)}


@router.post("/group/{group_id}/want")
def group_want(group_id: str):
    """Puts the group on the Movies wishlist (or returns its row): the base for 'search it as one file' and 'link the ThePornDB movie'."""
    info = _group(group_id)
    row = _group_film(group_id, info)
    history.event("info", "movies", f"Group {group_id} ({info['name']}) is on the Movies wishlist")
    return {"ok": True, "movie": row}


@router.post("/group/{group_id}/use-scenes")
def group_use_scenes(group_id: str):
    """The scenes of the last StashDB check that the group does not have yet go on the SCENE wishlist; when they are imported they join
    THIS group (no new group). Needs the check of this page visit."""
    import time as _time
    cached = _group_checks.get(str(group_id))
    if not cached or _time.time() - cached[0] > 3600:
        raise HTTPException(400, "run 'Check StashDB scenes' first")
    missing = [c for c in cached[1]["scenes"] if not c.get("owned")]
    if not missing:
        raise HTTPException(400, "every known StashDB scene is already in this group")
    info = _group(group_id)
    row = _group_film(group_id, info)
    if row["status"] in ("grabbed", "imported"):
        raise HTTPException(400, f"this movie is already '{row['status']}'")
    added = film_stashdb.put_on_wishlist(_db, row, missing)
    film_repo.update(_db, row["id"], mode="scenes", status="grabbed", scene_ids=[{"id": c["id"], "k": c["k"]} for c in missing],
                     grabbed_title=f"{len(missing)} StashDB scene(s) on the scene wishlist", import_error=None)
    history.event("info", "movies", f"Group {group_id}: {len(missing)} missing StashDB scene(s) put on the scene wishlist ({len(added)} new)")
    return {"ok": True, "scenes": len(missing), "new": len(added), "movie": film_repo.get(_db, row["id"])}


@router.get("/groups")
def multi_file_groups():
    """Stash groups with MORE THAN 3 files (normally a film split into scenes). One Stash request."""
    return {"groups": film_groups.list_groups(_get_stash_client())}


@router.post("/groups/{group_id}/check-stashdb")
def group_check_stashdb(group_id: str):
    """The StashDB scene check for an existing group -- ONE StashDB request, only on click. Display only: nothing is changed."""
    group = _group(group_id)
    client = _get_provider_client("stashdb")
    if client is None:
        raise HTTPException(503, "StashDB is not configured")
    try:
        import time as _time
        result = _enrich(film_groups.check_stashdb(client, group))
        _group_checks[str(group_id)] = (_time.time(), result)
        return result
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"StashDB check failed: {e}")


@router.get("/groups/{group_id}/name-preview")
def group_name_preview(group_id: str):
    """Dry run: the titles that would be set on the group's scenes WITHOUT a title (derived from the file names). Changes nothing."""
    return {"items": film_groups.name_preview(_group(group_id))}


class NameApplyBody(BaseModel):
    items: list[dict]


@router.post("/groups/{group_id}/name-apply")
def group_name_apply(group_id: str, body: NameApplyBody):
    """Sets the previewed titles (only on scenes that still have none); a backup file is written first."""
    result = film_groups.name_apply(_get_stash_client(), _group(group_id), body.items, _data_dir)
    if result.get("error"):
        raise HTTPException(400, result["error"])
    history.event("info", "movies", f"Group {group_id}: {len(result['applied'])} scene title(s) set from the file names",
                  details={"backup": result["backup"]})
    return result


class AssignBody(BaseModel):
    items: list[dict]


@router.post("/groups/{group_id}/assign")
def group_assign(group_id: str, body: AssignBody):
    """Assigns the group's scenes by hand: to a known StashDB scene (metadata applied like an import) or just a name. Backup first."""
    result = film_groups.assign(_get_stash_client(), _group(group_id), body.items, _data_dir)
    if result.get("error"):
        raise HTTPException(400, result["error"])
    history.event("info", "movies", f"Group {group_id}: {len(result['applied'])} scene(s) assigned by hand ({len(result['errors'])} failed)",
                  details={"backup": result["backup"]})
    return result


class AssignUndoBody(BaseModel):
    applied: list[dict]


@router.post("/groups/assign-undo")
def group_assign_undo(body: AssignUndoBody):
    return film_groups.assign_undo(_get_stash_client(), body.applied)


def _group_record(group_id):
    """The stored ThePornDB movie record of the film linked to this group (no ThePornDB request)."""
    row = film_repo.find_by_group(_db, group_id)
    full = film_repo.get(_db, row["id"], with_json=True) if row else None
    if not full or not full.get("tpdb_json"):
        raise HTTPException(400, "this group is not linked to a ThePornDB movie yet (Link ThePornDB movie...)")
    return full["tpdb_json"]


@router.get("/groups/{group_id}/metadata-preview")
def group_metadata_preview(group_id: str):
    """Dry run: the group's current values next to the linked ThePornDB movie's. No ThePornDB request; changes nothing."""
    result = film_groups.metadata_preview(_get_stash_client(), _get_movies(), group_id, _group_record(group_id))
    if result.get("error"):
        raise HTTPException(404, result["error"])
    return result


@router.get("/groups/{group_id}/scene-names")
def group_scene_names(group_id: str, source: str = ""):
    """Scene lists of the movie from our own scrapers: AdultDVDEmpire (titles + lengths; needs an AdultDVDEmpire URL on the group) and IAFD
    (performers per scene + bonus scenes, no titles/lengths; found from the group's name + year, or its iafd.com URL). ONE request to
    that site per call (cached for an hour), a length-based suggestion per local scene where the source has lengths. Changes nothing;
    applying goes through /groups/{id}/assign (titles and/or the position in the group)."""
    import scene_names
    group = _group(group_id)
    ade_url = scene_names.adult_empire_url(group["urls"])
    year = (group.get("date") or "")[:4]
    iafd_url = scene_names.iafd_url(group["urls"], group["name"], year)
    sources = [{"key": "adultempire", "label": "AdultDVDEmpire", "available": bool(ade_url)},
               {"key": "iafd", "label": "IAFD", "available": bool(iafd_url)}]
    key = source or ("adultempire" if ade_url else "iafd")
    if key == "adultempire":
        if not ade_url:
            return {"source": key, "sources": sources, "url": None, "scenes": [], "suggested": {}, "message": "This group has no AdultDVDEmpire URL."}
        try:
            scenes = scene_names.fetch(ade_url)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"AdultDVDEmpire could not be read: {e}")
        url, meta = ade_url, None
    elif key == "iafd":
        if not iafd_url:
            return {"source": key, "sources": sources, "url": None, "scenes": [], "suggested": {}, "message": "The group needs a name and a date (year) to find the movie at IAFD."}
        try:
            meta = scene_names.fetch_iafd(iafd_url)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"IAFD could not be read: {e}")
        scenes, url = meta["scenes"], meta["url"]
    else:
        raise HTTPException(400, "source must be adultempire or iafd")
    local = [{"id": x["id"], "minutes": x["minutes"]} for x in group["scene_list"]]
    extra = ""
    if meta:
        facts = [f'"{meta["title"]}"'] + ([f"{meta['minutes']} min"] if meta.get("minutes") else []) + ([meta["studio"]] if meta.get("studio") else [])
        extra = " IAFD: " + ", ".join(facts) + "." + (f" {meta['comments']}" if meta.get("comments") else "")
    return {"source": key, "sources": sources, "url": url, "scenes": scenes, "suggested": scene_names.suggest(local, scenes),
            "message": (f"{len(scenes)} scene(s) listed at {'IAFD' if key == 'iafd' else 'AdultDVDEmpire'}." if scenes else "The page lists no scenes.") + extra}


class MetadataApplyBody(BaseModel):
    keys: list[str]


@router.post("/groups/{group_id}/metadata-apply")
def group_metadata_apply(group_id: str, body: MetadataApplyBody):
    """Applies the ticked ThePornDB fields to the existing group; a backup file (fields + cover images) is written first."""
    result = film_groups.metadata_apply(_get_stash_client(), _get_movies(), group_id, _group_record(group_id), body.keys, _data_dir)
    if result.get("error"):
        raise HTTPException(400, result["error"])
    history.event("info", "movies", f"Group {group_id}: ThePornDB metadata applied ({', '.join(result['applied']['fields'])})",
                  details={"backup": result["backup"]})
    return result


class MetadataUndoBody(BaseModel):
    applied: dict


@router.post("/groups/metadata-undo")
def group_metadata_undo(body: MetadataUndoBody):
    return film_groups.metadata_undo(_get_stash_client(), body.applied)


class NameUndoBody(BaseModel):
    applied: list[dict]


@router.post("/groups/name-undo")
def group_name_undo(body: NameUndoBody):
    return film_groups.name_undo(_get_stash_client(), body.applied)


_SEED_IMAGES = "movie_groups_deleted_20261004_images"


@router.get("/{film_id}/seed-cover")
def seed_cover(film_id: int):
    """The cover of the deleted group a suggestion came from (the group no longer exists in Stash; its cover was saved to data/ when it was
    deleted). 404 when there is none."""
    film = film_repo.get(_db, film_id)
    ref = (film or {}).get("source_ref") or ""
    if not ref.startswith("deleted-group:"):
        raise HTTPException(404, "no saved cover")
    path = os.path.join(_data_dir, _SEED_IMAGES, f"{ref.split(':', 1)[1]}_front.img")
    if not os.path.isfile(path):
        raise HTTPException(404, "no saved cover")
    with open(path, "rb") as fh:
        data = fh.read()
    kind = ("image/png" if data[:4] == b"\x89PNG" else "image/gif" if data[:3] == b"GIF" else
            "image/webp" if data[:4] == b"RIFF" else "image/jpeg")
    return Response(content=data, media_type=kind, headers={"Cache-Control": "max-age=86400"})


class CandidateBody(BaseModel):
    tpdb_id: str


@router.post("/{film_id}/use-candidate")
def use_candidate(film_id: int, body: CandidateBody):
    """Takes one of the candidates stored by the last match (no new search): title, year, studio, length, cover of that ThePornDB movie."""
    film = film_repo.get(_db, film_id)
    if not film:
        raise HTTPException(404, "movie not found")
    record = film_repo.candidate_record(_db, film_id, body.tpdb_id)
    if not record:
        raise HTTPException(404, "that candidate is not stored for this movie any more -- use 'Link movie…'")
    other = film_repo.find_duplicate(_db, body.tpdb_id, None, None)
    if other and other != film_id:
        raise HTTPException(409, "this ThePornDB movie is already on the movie wishlist")
    film_repo.update(_db, film_id, **_movie_fields(record), match_class="linked", note=None)
    return {"ok": True, "movie": film_repo.get(_db, film_id)}


class BulkStatusBody(BaseModel):
    ids: list[int]
    status: str
    from_status: str = ""


@router.post("/bulk-status")
def bulk_status(body: BulkStatusBody):
    """Accept / un-accept / dismiss many movies at once ("Accept all sure", the calendar's "Dismiss N shown" and their Undo). Only touches rows that
    are in `from_status` (default: suggested -> wanted and wanted -> suggested)."""
    if body.status not in ("wanted", "suggested", "dismissed"):
        raise HTTPException(400, "status must be wanted, suggested or dismissed")
    from_status = body.from_status or ("suggested" if body.status == "wanted" else "wanted")
    if from_status not in ("wanted", "suggested", "dismissed"):
        raise HTTPException(400, "from_status must be wanted, suggested or dismissed")
    changed = 0
    changed_ids = []
    for fid in body.ids:
        film = film_repo.get(_db, fid)
        if film and film["status"] == from_status:
            film_repo.update(_db, fid, status=body.status)
            changed += 1
            changed_ids.append(fid)
    if changed:
        what = {"wanted": "accepted", "suggested": "set back to suggestions", "dismissed": "dismissed"}[body.status]
        history.event("info", "movies", f"{changed} movie(s) {what} at once")
        if body.status == "wanted":
            _kick_whisparr(changed_ids)
    return {"ok": True, "changed": changed}


@router.post("/rematch")
def rematch():
    """Matches the unclear suggestions (likely / unsure / no match) on ThePornDB again -- ONE request each, in the background -- and keeps
    the candidates. What the user linked is skipped. Progress: GET /films/seed-match/status."""
    rows = film_repo.list_films(_db, ["suggested"], per_page=0)["rows"]
    result = film_match.start_rematch(_seed_job, rows, _data_dir)
    if result.get("error"):
        raise HTTPException(409, result["error"])
    return result


class SeedBody(BaseModel):
    source: str = "deleted-groups"


@router.post("/seed-match")
def seed_match(body: SeedBody):
    """Looks up the seed films on ThePornDB (ONE request per film, ~0.8 s apart), in the background. Results appear as 'suggested'."""
    try:
        if body.source == "deleted-groups":
            seeds = film_match.seeds_from_deleted_groups(_data_dir)
        elif body.source == "empty-groups":
            seeds = film_match.seeds_from_empty_groups(_get_movies())
        else:
            raise HTTPException(400, "source must be deleted-groups or empty-groups")
    except FileNotFoundError:
        raise HTTPException(404, "seed file not found")
    result = _seed_job.start(seeds, body.source)
    if result.get("error"):
        raise HTTPException(409, result["error"])
    return result


@router.get("/seed-match/status")
def seed_match_status():
    return _seed_job.state
