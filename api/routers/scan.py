from fastapi import APIRouter

router = APIRouter(prefix="/scan", tags=["scan"])

_sync_and_scan = None
_last_result = None
_progress = {"phase": "idle", "current": 0, "total": 0, "entity_name": None}


def init_scan_router(sync_and_scan):
    global _sync_and_scan
    _sync_and_scan = sync_and_scan


def set_progress(phase, current=None, total=None, entity_name=None):
    """Called from main.py while a scan is running, from its own
    background thread -- lets /scan/status report live progress without
    waiting for the whole run to finish. Added 2026-09-12 after being
    unable to tell how far into a multi-hour run (watchlist grew to
    2000+ entities) the scan actually was, see PROGRESS.md's "Scan
    progress counter" section. `current`/`total` default to whatever
    was last set (not reset to 0) so a phase-only update -- e.g.
    switching to "idle" once a scan completes -- doesn't lose the final
    count."""
    global _progress
    if current is None:
        current = _progress["current"]
    if total is None:
        total = _progress["total"]
    _progress = {"phase": phase, "current": current, "total": total, "entity_name": entity_name}


@router.post("/run")
def run_now():
    """Manual "run now" trigger -- mirrors the scheduled scan the
    background loop runs automatically (see main.py). Syncs the
    watchlist from Stash's current Favorite studios/performers first,
    then scans with a fresh StashDB client (so a key just saved on the
    Settings page takes effect immediately, no restart needed) and the
    current stashdb_query_delay setting."""
    global _last_result
    _last_result = _sync_and_scan()
    return _last_result


@router.get("/status")
def status():
    return {"last_result": _last_result, "progress": _progress}
