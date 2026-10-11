from fastapi import APIRouter

router = APIRouter(prefix="/whisparr-blocklist-sync", tags=["whisparr-blocklist-sync"])

_run_sync = None
_last_result = None


def init_whisparr_blocklist_sync_router(run_sync):
    global _run_sync
    _run_sync = run_sync


@router.post("/sync")
def sync_now():
    """Manual "sync from Whisparr" trigger -- mirrors the scheduled
    background loop (see main.py), same locked/skip-if-running pattern
    as /stashdb-favorites/sync."""
    global _last_result
    _last_result = _run_sync()
    return _last_result


@router.get("/status")
def status():
    return {"last_result": _last_result}
