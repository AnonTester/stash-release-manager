"""Manual trigger + status for the periodic indexer re-scan loop
(Milestone 3, 2026-09-19) -- same pattern as routers/import_check.py."""
from fastapi import APIRouter

router = APIRouter(prefix="/indexer-search", tags=["indexer-search"])

_run_search = None
_last_result = None
_list_stuck = None


def init_indexer_search_router(run_search, list_stuck=None):
    global _run_search, _list_stuck
    _run_search = run_search
    _list_stuck = list_stuck


@router.post("/run")
def run_now():
    global _last_result
    _last_result = _run_search()
    return _last_result


@router.get("/status")
def status():
    return {"last_result": _last_result}


@router.get("/stuck")
def stuck_preview():
    """Dry run of "Search all stuck now": which releases it would query (one indexer request each) -- nothing is searched."""
    rows = _list_stuck() if _list_stuck else []
    return {"count": len(rows), "releases": [
        {"provider": r["provider"], "scene_stashdb_id": r["scene_stashdb_id"], "title": r.get("title"), "studio": r.get("studio_name"),
         "last_search_result": r.get("last_search_result")} for r in rows]}


@router.post("/run-stuck")
def run_stuck_now():
    """One targeted search for every stuck release, right now (the button after its confirmation)."""
    import threading
    rows = _list_stuck() if _list_stuck else []
    if not rows:
        return {"started": False, "count": 0}

    def work():
        global _last_result
        _last_result = _run_search(only_stuck=True)

    threading.Thread(target=work, daemon=True, name="search-all-stuck").start()  # 40+ indexer queries: runs in the background, the result lands in History
    return {"started": True, "count": len(rows)}
