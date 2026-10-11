from fastapi import APIRouter

router = APIRouter(prefix="/import-check", tags=["import-check"])

_run_check = None
_last_result = None


def init_import_check_router(run_check):
    global _run_check
    _run_check = run_check


@router.post("/run")
def run_now():
    """Manual "check import status" trigger -- same pattern as
    /scan/run and /stashdb-favorites/sync."""
    global _last_result
    _last_result = _run_check()
    return _last_result


@router.get("/status")
def status():
    return {"last_result": _last_result}
