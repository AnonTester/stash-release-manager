"""Manual trigger + status for the periodic indexer re-scan loop
(Milestone 3, 2026-09-19) -- same pattern as routers/import_check.py."""
from fastapi import APIRouter

router = APIRouter(prefix="/indexer-search", tags=["indexer-search"])

_run_search = None
_last_result = None


def init_indexer_search_router(run_search):
    global _run_search
    _run_search = run_search


@router.post("/run")
def run_now():
    global _last_result
    _last_result = _run_search()
    return _last_result


@router.get("/status")
def status():
    return {"last_result": _last_result}
