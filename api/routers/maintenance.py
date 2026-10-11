"""Maintenance page: retroactive Library Check + bulk correction
(2026-09-16, see PROGRESS.md's "Maintenance page" section).

Backed by library_check.py's shared logic (also used standalone by
scripts/audit_library_fingerprints.py and
scripts/fix_library_mismatches.py) and library_check_repo.py's DB
persistence (library_check_run/library_check_finding, see db.py).

Deliberately does NOT use scan.py's pattern of letting FastAPI's own
threadpool block the triggering HTTP call until the whole job
finishes -- a full check took ~9 minutes against the real library
(2026-09-15/16), and the Maintenance page needs a genuinely live,
Stash-Tagger-style progress bar while it runs. `run_now()` below
explicitly spawns its own `threading.Thread` and returns immediately;
`/library-check/status` reads the run row's live progress from the DB,
pollable independently the whole time."""
import logging
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import library_check
import library_check_repo
import release_repo
import library_rename
import upgrades
import vr_check

router = APIRouter(prefix="/maintenance", tags=["maintenance"])

logger = logging.getLogger(__name__)
_db = None
_get_stash_client = None
_get_stashdb_client = None
_get_provider_client = None
_to_local = None
_organizer = None
_importer = None
_movies = None
_check_lock = threading.Lock()


_vr_job = None
_fix_wrong_download = None


_upgrade_search = None
_cutoff_resolution = None


def init_maintenance_router(db, get_stash_client, get_stashdb_client, vr_job=None, fix_wrong_download=None,
                            upgrade_search=None, cutoff_resolution=None, get_provider_client=None, to_local=None,
                            organizer_factory=None, importer_factory=None, movies_factory=None):
    global _db, _get_stash_client, _get_stashdb_client, _vr_job, _fix_wrong_download
    global _upgrade_search, _cutoff_resolution, _get_provider_client, _to_local, _organizer, _importer, _movies
    _importer = importer_factory
    _movies = movies_factory
    _get_provider_client = get_provider_client
    _to_local = to_local
    _organizer = organizer_factory
    _db = db
    _get_stash_client = get_stash_client
    _get_stashdb_client = get_stashdb_client
    _vr_job = vr_job
    _fix_wrong_download = fix_wrong_download
    _upgrade_search = upgrade_search
    _cutoff_resolution = cutoff_resolution


# --- Upgrade search (2026-09-26, upgrades.py) ---

@router.get("/upgrades")
def upgrades_list():
    from routers.settings import get_setting_value
    candidates = upgrades.list_candidates(_db, ("wanted", "grabbed", "review", "done"))
    # Studio + release date for the manual search prefill ("Studio.YY.MM.DD"), from the release rows when the scene has any.
    with _db.connect() as conn:
        info = {(r["provider"], r["scene_stashdb_id"]): (r["studio_name"], r["scene_date"]) for r in conn.execute(
            "SELECT provider, scene_stashdb_id, studio_name, scene_date FROM release WHERE studio_name != ''")}
    for c in candidates:
        c["studio_name"], c["scene_date"] = info.get((c.get("provider"), c.get("provider_scene_id")), (None, None))
    return {
        "candidates": candidates,
        "enabled": get_setting_value(_db, "upgrade_search_enabled", "false") == "true",
        "per_run": get_setting_value(_db, "upgrade_search_per_run", "20"),
        "interval_hours": get_setting_value(_db, "upgrade_search_interval_hours", "12"),
        "cutoff_resolution": _cutoff_resolution() if _cutoff_resolution else None,
    }


@router.post("/upgrades/refresh")
def upgrades_refresh():
    from routers.settings import get_setting_value
    return upgrades.refresh_candidates(_db, _get_stash_client(), lambda k, d: get_setting_value(_db, k, d),
                                       _cutoff_resolution() if _cutoff_resolution else None)


@router.post("/upgrades/{cand_id}/search")
def upgrades_search(cand_id: int, dry_run: bool = True):
    return _upgrade_search(cand_id, dry_run=dry_run)


# --- Organize the library: rename / move (2026-09-28, library_organize.py) ---

@router.get("/organize/studios")
def organize_studios(refresh: bool = False):
    return {"studios": _organizer().overview(refresh=refresh)}


@router.get("/organize/plan")
def organize_plan(studio_id: str):
    return _organizer().plan(studio_id)


@router.post("/organize/apply")
def organize_apply(body: dict):
    kinds = [k for k in (body.get("kinds") or ["rename", "move"]) if k in ("rename", "move")]
    return _organizer().apply(body.get("studio_id"), body.get("file_ids") or [], kinds=kinds, force=bool(body.get("force")))


# --- Folder import: /data/Scenes, /data/import, /data/_VR ... (2026-09-29, library_import.py) ---

@router.get("/import/sources")
def import_sources():
    return {"sources": _importer().sources()}


@router.get("/import/analyse")
def import_analyse(root: str):
    importer = _importer()
    if root.rstrip("/") not in importer.roots():
        raise HTTPException(status_code=400, detail="not an import source")
    return importer.analyse(root)


@router.post("/import/apply")
def import_apply(body: dict):
    importer = _importer()
    root = (body.get("root") or "").rstrip("/")
    if root not in importer.roots():
        raise HTTPException(status_code=400, detail="not an import source")
    return importer.apply(root, body.get("scene_ids") or [], picks=body.get("picks") or {}, force=bool(body.get("force")))


@router.post("/import/organized-hook")
def import_organized_hook(body: dict):
    """The plugin's Scene.Update.Post hook calls this when a scene was marked Organized in Stash (v0.191.0): what happens is decided by the
    setting organized_hook (off / preview / on), see library_import.Importer.organized_scene()."""
    importer = _importer()
    scene_id = body.get("scene_id")
    if scene_id is None:
        raise HTTPException(status_code=400, detail="scene_id is required")
    return importer.organized_scene(scene_id, (importer.setting("organized_hook") or "off").strip())


@router.get("/import/finish-check")
def import_finish_check(root: str):
    return _importer().finish_check(root)


@router.post("/import/finish")
def import_finish(body: dict):
    importer = _importer()
    root = (body.get("root") or "").rstrip("/")
    if root not in importer.roots():
        raise HTTPException(status_code=400, detail="not an import source")
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="confirm=true required")
    return importer.finish_source(root, importer.backup_dir)


# --- Movies & groups lists (2026-09-29, library_movies.py) ---

@router.get("/movies/no-group")
def movies_no_group():
    return {"folders": _movies().videos_without_group()}


@router.post("/movies/create-group")
def movies_create_group(body: dict):
    try:
        return _movies().create_group(body.get("title"), body.get("scene_ids") or [], group_id=body.get("group_id"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/movies/performers-empty")
def movies_performers_empty():
    return {"performers": _movies().performers_without_scenes()}


@router.get("/movies/groups-empty")
def movies_groups_empty():
    return {"groups": _movies().groups_without_files()}


@router.post("/import/suggest")
def import_suggest(body: dict):
    importer = _importer()
    root = (body.get("root") or "").rstrip("/")
    if root not in importer.roots():
        raise HTTPException(status_code=400, detail="not an import source")
    return {"suggestions": importer.suggest(root, body.get("scene_ids") or [])}


# --- File names with "Unknown" as quality (2026-09-28, library_rename.py) ---

@router.get("/unknown-quality")
def unknown_quality():
    return {"items": library_rename.proposals(_db, _get_stash_client())}


@router.post("/unknown-quality/apply")
def unknown_quality_apply(body: dict):
    return library_rename.apply(_db, _get_stash_client(), _to_local, body.get("paths") or [])


@router.post("/upgrades/{cand_id}/review/accept")
def upgrades_review_accept(cand_id: int):
    """Activity > Needs review (2026-09-28): grab the stored candidate."""
    return upgrades.accept_review(_db, cand_id, _get_provider_client)


@router.post("/upgrades/{cand_id}/review/reject")
def upgrades_review_reject(cand_id: int):
    return upgrades.reject_review(_db, cand_id)


@router.post("/upgrades/{cand_id}/manual-grab")
def upgrades_manual_grab(cand_id: int, body: dict):
    """Grab of a result the user picked from a typed search on Upgrade wanted (the search itself is releases/.../manual-search)."""
    result = upgrades.manual_grab(_db, cand_id, _get_provider_client, body.get("candidate") or {})
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result


@router.post("/upgrades/{cand_id}/dismiss")
def upgrades_dismiss(cand_id: int):
    cand = upgrades.get_candidate(_db, cand_id)
    upgrades.set_candidate(_db, cand_id, status="dismissed")
    return {"ok": True, "previous": cand["status"] if cand else None}


class RestoreBody(BaseModel):
    status: str


@router.post("/upgrades/{cand_id}/restore")
def upgrades_restore(cand_id: int, body: RestoreBody):
    """Undo of "Dismiss" on Upgrade wanted: back to the status the candidate had before."""
    cand = upgrades.get_candidate(_db, cand_id)
    if not cand or cand["status"] != "dismissed" or body.status not in ("wanted", "grabbed", "review"):
        raise HTTPException(400, "nothing to restore")
    upgrades.set_candidate(_db, cand_id, status=body.status)
    return {"ok": True}


# --- VR check: "VR scene, but the file is 2D" (2026-09-26, vr_check.py) ---

@router.post("/vr-check/run")
def vr_check_run(mode: str = "quick", force: bool = False):
    if mode not in ("quick", "full"):
        raise HTTPException(400, "mode must be 'quick' or 'full'")
    return _vr_job.start(mode, force)


@router.get("/vr-check/status")
def vr_check_status():
    return {"job": _vr_job.status, "last_run": vr_check.last_run(_db)}


def _sidecar_release(finding):
    """The release THIS sidecar downloaded for the finding's scene, if any
    -- only then can "Fix wrong download" blocklist + re-search it."""
    if not finding.get("provider_scene_id"):
        return None
    with _db.connect() as conn:
        row = conn.execute(
            """SELECT provider, scene_stashdb_id, entity_type, entity_stashdb_id FROM release
               WHERE provider = ? AND scene_stashdb_id = ? AND download_client_id IS NOT NULL
                 AND status IN ('imported', 'import_failed') LIMIT 1""",
            (finding["provider"], finding["provider_scene_id"])).fetchone()
    return dict(row) if row else None


@router.get("/vr-check/findings")
def vr_check_findings(status: str = "pending"):
    if status in ("pending", "dismissed"):
        try:
            vr_check.prune_stale(_db, _get_stash_client())
        except Exception:
            logger.exception("VR check: couldn't prune stale findings")
    findings = vr_check.list_findings(_db, status)
    for f in findings:
        f["sidecar_release"] = _sidecar_release(f)
    return {"findings": findings}


@router.get("/vr-check/findings/{finding_id}/frame")
def vr_check_frame(finding_id: int):
    """A frame from the middle of the flagged file as a data: URL (goes
    through the plugin bridge like every other call, hence JSON)."""
    import base64
    finding = vr_check.get_finding(_db, finding_id)
    if finding is None:
        raise HTTPException(404, "finding not found")
    try:
        jpeg = vr_check.frame_jpeg(_vr_job.to_local_path(finding["file_path"]))
    except Exception as e:
        raise HTTPException(500, f"couldn't read a frame: {e}")
    return {"data_url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}


@router.post("/vr-check/findings/{finding_id}/dismiss")
def vr_check_dismiss(finding_id: int):
    finding = vr_check.get_finding(_db, finding_id)
    vr_check.set_finding_status(_db, finding_id, "dismissed")
    return {"ok": True, "previous": finding["finding_status"] if finding else None}


class VerdictBody(BaseModel):
    verdict: str


@router.post("/vr-check/findings/{finding_id}/verdict")
def vr_check_set_verdict(finding_id: int, body: VerdictBody):
    """The user's own verdict for a file: 2D ('mono'), 'vr_mono' or 'unclear' (v0.105.0). Kept over re-judging/re-analysis of the
    same file. Answers with the previous state for the Undo."""
    if body.verdict not in vr_check.MANUAL_VERDICTS:
        raise HTTPException(400, "verdict must be one of: " + ", ".join(vr_check.MANUAL_VERDICTS))
    before = vr_check.set_verdict(_db, finding_id, body.verdict)
    if before is None:
        raise HTTPException(404, "finding not found")
    return {"ok": True, "previous": before}


class VerdictRestoreBody(BaseModel):
    verdict: str
    manual: bool = False
    finding_status: str = "pending"


@router.post("/vr-check/findings/{finding_id}/verdict-restore")
def vr_check_restore_verdict(finding_id: int, body: VerdictRestoreBody):
    if not vr_check.get_finding(_db, finding_id):
        raise HTTPException(404, "finding not found")
    vr_check.restore_verdict(_db, finding_id, body.verdict, body.manual, body.finding_status)
    return {"ok": True}


@router.post("/vr-check/findings/{finding_id}/restore")
def vr_check_restore(finding_id: int, body: RestoreBody):
    """Undo of "Dismiss" on a VR finding: back to the status it had before."""
    finding = vr_check.get_finding(_db, finding_id)
    if not finding or finding["finding_status"] != "dismissed" or body.status in ("dismissed", "none", ""):
        raise HTTPException(400, "nothing to restore")
    vr_check.set_finding_status(_db, finding_id, body.status)
    return {"ok": True}


@router.post("/vr-check/findings/{finding_id}/fix")
def vr_check_fix(finding_id: int):
    """"Fix wrong download" for a flagged file this sidecar downloaded:
    blocklist the release, move the file to the recycle bin, reset the
    release so it gets searched again (file_importer.fix_wrong_download()
    with the flagged file's own path)."""
    finding = vr_check.get_finding(_db, finding_id)
    if finding is None:
        raise HTTPException(404, "finding not found")
    release = _sidecar_release(finding)
    if release is None:
        raise HTTPException(409, "not downloaded by this sidecar -- nothing to re-search")
    result = _fix_wrong_download(release["provider"], release["scene_stashdb_id"], release["entity_type"],
                                 release["entity_stashdb_id"], finding["file_path"])
    vr_check.set_finding_status(_db, finding_id, "fixed")
    return result


@router.get("/library-paths")
def library_paths():
    """Stash's own configured top-level library directories -- shown
    as quick-jump shortcuts above the directory browser (see
    /directory-browse below), same starting points Stash's native
    task dialogs offer before you navigate into a subdirectory."""
    return {"paths": _get_stash_client().list_library_paths()}


@router.get("/directory-browse")
def directory_browse(path: str | None = None):
    """Backs the Maintenance page's directory picker -- mirrors Stash's
    own "Select folders" dialog (used by Scan/Clean/Generate's
    "Selective..." buttons) exactly: browse via `directory`, add the
    current path to a selection list client-side (see
    stash_client.browse_directory())."""
    return _get_stash_client().browse_directory(path)


def _run_check(run_id, paths):
    stash_client = _get_stash_client()
    stashdb_client = _get_stashdb_client()
    try:
        scenes = library_check.list_scenes_in_scope(stash_client, paths)
        library_check_repo.update_run_progress(_db, run_id, 0, len(scenes))
        matches, mismatches, ambiguous, unverifiable = library_check.find_library_mismatches(
            stashdb_client, scenes, stash_client.stashdb_endpoint,
            progress_cb=lambda current, total: library_check_repo.update_run_progress(_db, run_id, current, total),
        )
        for m in mismatches:
            library_check_repo.insert_finding(
                _db, run_id, m["id"], m["title"], "mismatch",
                expected_stashdb_id=m["expected_stashdb_id"], candidates=m["found_as"], file_paths=m["paths"],
            )
        for a in ambiguous:
            library_check_repo.insert_finding(
                _db, run_id, a["id"], a["title"], "ambiguous",
                expected_stashdb_id=a["expected_stashdb_id"], candidates=a["found_as"], file_paths=a["paths"],
            )
        for u in unverifiable:
            library_check_repo.insert_finding(
                _db, run_id, u["id"], u["title"], "unverifiable", reason=u["reason"], file_paths=u["paths"],
            )
        library_check_repo.finish_run(
            _db, run_id, "done",
            match_count=len(matches), mismatch_count=len(mismatches), unverifiable_count=len(unverifiable) + len(ambiguous),
        )
    except Exception as e:
        library_check_repo.finish_run(_db, run_id, "failed", error=str(e))
    finally:
        _check_lock.release()


@router.post("/library-check/run")
def run_now(body: dict | None = None):
    paths = (body or {}).get("paths") or []
    if not _check_lock.acquire(blocking=False):
        return {"skipped": True, "reason": "a library check is already running"}
    run_id = library_check_repo.create_run(_db, paths)
    threading.Thread(target=_run_check, args=(run_id, paths), daemon=True).start()
    return {"run_id": run_id}


@router.get("/library-check/status")
def status():
    run = library_check_repo.get_latest_run(_db)
    if not run:
        return {"phase": "idle"}
    phase = "running" if run["status"] == "running" else run["status"]
    return {
        "phase": phase,
        "run_id": run["id"],
        "current": run["current"],
        "total": run["total"],
        "match_count": run["match_count"],
        "mismatch_count": run["mismatch_count"],
        "unverifiable_count": run["unverifiable_count"],
        "error": run["error"],
    }


@router.get("/library-check/findings")
def findings(status: str = "pending", kind: str | None = None):
    return {"findings": library_check_repo.list_findings(_db, status=status, kind=kind)}


@router.post("/library-check/findings/{finding_id}/apply")
def apply_finding(finding_id: int):
    finding = library_check_repo.get_finding(_db, finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="finding not found")
    if finding["kind"] != "mismatch":
        raise HTTPException(status_code=400, detail=f"finding is '{finding['kind']}', not an actionable mismatch")
    candidates = finding["candidates"]
    target_id = candidates[0]["id"]
    try:
        result = library_check.apply_fix(_get_stash_client(), finding["scene_id"], target_id)
        library_check_repo.set_finding_status(_db, finding_id, "applied", applied_title=result["title"])
        return {"applied": True, "title": result["title"]}
    except Exception as e:
        library_check_repo.set_finding_status(_db, finding_id, "failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/library-check/findings/{finding_id}/dismiss")
def dismiss_finding(finding_id: int):
    finding = library_check_repo.get_finding(_db, finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="finding not found")
    library_check_repo.set_finding_status(_db, finding_id, "dismissed")
    return {"dismissed": True, "previous": finding.get("status")}


@router.post("/library-check/findings/{finding_id}/restore")
def restore_finding(finding_id: int, body: RestoreBody):
    """Undo of "Dismiss" on a Library-check finding: back to 'pending' (the only status Dismiss is offered from)."""
    finding = library_check_repo.get_finding(_db, finding_id)
    if not finding or finding.get("status") != "dismissed" or body.status != "pending":
        raise HTTPException(400, "nothing to restore")
    with _db.connect() as conn:
        conn.execute("UPDATE library_check_finding SET status = 'pending', resolved_at = NULL WHERE id = ?", (finding_id,))
    return {"ok": True}
