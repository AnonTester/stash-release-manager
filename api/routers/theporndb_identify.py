from fastapi import APIRouter

router = APIRouter(prefix="/theporndb-identify", tags=["theporndb-identify"])

_run_identify = None
_last_result = None


def init_theporndb_identify_router(run_identify):
    global _run_identify
    _run_identify = run_identify


@router.post("/sync")
def sync_now():
    """Manual "identify against ThePornDB" trigger -- mirrors the
    scheduled background loop (see main.py), same locked/skip-if-running
    pattern as /scan/run and /stashdb-favorites/sync."""
    global _last_result
    _last_result = _run_identify()
    return _last_result


@router.get("/status")
def status():
    return {"last_result": _last_result}
