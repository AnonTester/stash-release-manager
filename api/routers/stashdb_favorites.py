from fastapi import APIRouter

router = APIRouter(prefix="/stashdb-favorites", tags=["stashdb-favorites"])

_run_import = None
_last_result = None


def init_stashdb_favorites_router(run_import):
    global _run_import
    _run_import = run_import


@router.post("/sync")
def sync_now():
    """Manual "sync from StashDB" trigger -- mirrors the scheduled
    background loop (see main.py), same locked/skip-if-running pattern
    as /scan/run."""
    global _last_result
    _last_result = _run_import()
    return _last_result


@router.get("/status")
def status():
    return {"last_result": _last_result}
