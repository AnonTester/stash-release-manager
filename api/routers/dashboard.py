"""Read-only endpoints for a homelab dashboard (v0.261.0): what Activity shows in a few numbers + short lists, and the release calendar in the
shape of the Radarr / Sonarr calendar. Nothing here changes anything, and nothing carries a download URL or an API key."""
import datetime

from fastapi import APIRouter, HTTPException

import film_repo
import release_repo

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_db = None
_download_progress = None  # routers.releases.download_progress
_reviews = None  # routers.releases.reviews


def init_dashboard_router(db, download_progress, reviews):
    global _db, _download_progress, _reviews
    _db, _download_progress, _reviews = db, download_progress, reviews


def _scene_info(scene_id):
    """Title / studio / date of a scene by its provider id (the progress map is keyed by scene id only)."""
    with _db.connect() as conn:
        row = conn.execute("SELECT provider, title, studio_name, COALESCE(scene_date, substr(scene_created, 1, 10)) AS day, is_vr "
                           "FROM release WHERE scene_stashdb_id = ? LIMIT 1", (scene_id,)).fetchone()
    return dict(row) if row else {}


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@router.get("/activity")
def activity():
    """What Activity shows, as numbers and short lists: downloading, importing (the file is being checked / copied / encoded / waits for its
    import), needs review (a candidate waits for a yes / no) and failed (import failed). Scenes and movies together; "kind" says which."""
    progress = _download_progress() or {}
    films = {f"film:{f['id']}": f for f in film_repo.list_films(_db, ["grabbed"], per_page=0)["rows"]}
    downloading, importing = [], []
    for key, p in progress.items():
        if key.startswith("film:"):
            f = films.get(key) or {}
            item = {"kind": "movie", "id": key, "title": f.get("title") or key, "studio": f.get("studio_name"), "date": f.get("release_date")}
        else:
            info = _scene_info(key)
            item = {"kind": "scene", "id": key, "provider": info.get("provider"), "title": info.get("title") or key,
                    "studio": info.get("studio_name"), "date": info.get("day"), "vr": bool(info.get("is_vr"))}
        phase = p.get("phase")
        item["phase"] = phase
        if phase in ("downloading", "torrent_problem"):
            item.update(percentage=_num(p.get("percentage")), time_left=p.get("timeleft"), size_mb=_num(p.get("mb")),
                        left_mb=_num(p.get("mbleft")), client_status=p.get("status"))
            if phase == "torrent_problem":
                item["problem"] = p.get("health_detail") or p.get("health")
            downloading.append(item)
        else:
            if p.get("percentage") is not None:
                item["percentage"] = _num(p.get("percentage"))
            if p.get("seconds_left") is not None:
                item["seconds_left"] = p.get("seconds_left")
            importing.append(item)

    needs_review = []
    rv = _reviews() or {}
    for r in rv.get("wishlist") or []:
        c = r.get("review_candidate") or {}
        needs_review.append({"kind": "scene", "id": r.get("scene_stashdb_id"), "provider": r.get("provider"), "title": r.get("title"),
                             "studio": r.get("studio_name"), "date": r.get("scene_date"), "candidate": c.get("title"), "upgrade": False})
    for r in rv.get("upgrades") or []:
        c = r.get("review_candidate") or {}
        needs_review.append({"kind": "scene", "id": r.get("provider_scene_id") or r.get("scene_id"), "provider": r.get("provider"),
                             "title": r.get("scene_title"), "candidate": c.get("title"), "upgrade": True})
    try:
        for f in film_repo.list_films(_db, ["wanted", "imported"], per_page=0)["rows"]:
            if f.get("review_candidate") and (f["status"] == "wanted" or f.get("upgrade_wanted")):
                needs_review.append({"kind": "movie", "id": f"film:{f['id']}", "title": f.get("title"), "studio": f.get("studio_name"),
                                     "date": f.get("release_date"), "candidate": (f["review_candidate"] or {}).get("title") if isinstance(f["review_candidate"], dict) else None,
                                     "upgrade": f["status"] == "imported"})
    except Exception:  # noqa: BLE001 -- movies are optional
        pass

    failed = []
    seen = set()
    for r in release_repo.query_releases(_db, statuses=("import_failed",), per_page=0)["rows"]:
        k = (r["provider"], r["scene_stashdb_id"])
        if k in seen:
            continue
        seen.add(k)
        failed.append({"kind": "scene", "id": r["scene_stashdb_id"], "provider": r["provider"], "title": r["title"], "studio": r["studio_name"],
                       "date": r.get("scene_date"), "error_code": r.get("import_error_code"), "error": r.get("import_error_detail")})
    for f in film_repo.list_films(_db, ["import_failed"], per_page=0)["rows"]:
        failed.append({"kind": "movie", "id": f"film:{f['id']}", "title": f["title"], "studio": f.get("studio_name"),
                       "date": f.get("release_date"), "error_code": None, "error": f.get("import_error")})

    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "counts": {"downloading": len(downloading), "importing": len(importing), "needs_review": len(needs_review), "failed": len(failed)},
        "downloading": downloading, "importing": importing, "needs_review": needs_review, "failed": failed,
    }


# What a calendar entry is, in Sonarr's words: downloaded / downloading / wanted (on the wishlist, not there yet) / failed / available (a
# new release of a watched studio or performer that is not on the wishlist).
def _state(row, downloading_ids):
    s = row["status"]
    if s == "imported":
        return "downloaded"
    if s == "import_failed":
        return "failed"
    if s == "sent_to_whisparr":
        return "downloading" if (row.get("download_job_id") or row["scene_stashdb_id"] in downloading_ids) else "wanted"
    return "available"


@router.get("/calendar")
def calendar(start: str = "", end: str = "", include_available: bool = False, vr: bool | None = None):
    """The release calendar between two dates (inclusive, YYYY-MM-DD; default: from 7 days ago to 30 days ahead; at most 366 days), like
    Radarr's / Sonarr's calendar: `days` has one entry per day that has releases, each with its items, `items` is the same flat.
    state: downloaded | downloading | wanted | failed | available. By default only what is on the wishlist or in the library; `include_available=true`
    adds the new releases of watched studios / performers that were not added. `vr` filters VR (true) or flat (false)."""
    today = datetime.date.today()
    try:
        d_from = datetime.date.fromisoformat(start) if start else today - datetime.timedelta(days=7)
        d_to = datetime.date.fromisoformat(end) if end else today + datetime.timedelta(days=30)
    except ValueError:
        raise HTTPException(400, "start and end must be dates like 2026-11-21")
    if d_to < d_from or (d_to - d_from).days > 366:
        raise HTTPException(400, "end must not be before start, and the range is at most 366 days")
    statuses = ("sent_to_whisparr", "imported", "import_failed") + (("new",) if include_available else ())
    rows = release_repo.query_releases(_db, statuses=statuses, date_from=d_from.isoformat(), date_until=d_to.isoformat(), per_page=0,
                                       vr=vr)["rows"]
    downloading_ids = {r["scene_stashdb_id"] for r in release_repo.list_grabbed_awaiting_import(_db)}
    items, seen = [], {}
    order = {"downloaded": 0, "downloading": 1, "failed": 2, "wanted": 3, "available": 4}
    for r in rows:
        key = (r["provider"], r["scene_stashdb_id"])
        state = _state(r, downloading_ids)
        if key in seen:  # one scene can have a row per watched entity: the furthest state wins
            if order[state] < order[seen[key]["state"]]:
                seen[key]["state"] = state
            continue
        day = r.get("scene_date") or str(r.get("scene_created") or "")[:10]
        item = {"id": r["scene_stashdb_id"], "provider": r["provider"], "title": r["title"], "date": day, "studio": r["studio_name"],
                "vr": bool(r.get("is_vr")), "state": state, "image_url": r.get("image_url")}
        seen[key] = item
        items.append(item)
    items.sort(key=lambda i: (i["date"] or "", i["studio"] or "", i["title"] or ""))
    days = {}
    for it in items:
        days.setdefault(it["date"], []).append(it)
    return {"start": d_from.isoformat(), "end": d_to.isoformat(), "total": len(items),
            "days": [{"date": d, "items": v} for d, v in days.items()], "items": items}
