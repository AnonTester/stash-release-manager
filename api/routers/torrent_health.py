"""The torrent monitor's state for the Activity page and the actions on a flagged torrent (v0.231.0) -- see torrent_health.py. A torrent is
addressed by its client id and hash (the monitor's own key); no response contains a link or key."""
import threading
import time

from fastapi import APIRouter

import torrent_health

router = APIRouter(prefix="/torrent-health", tags=["torrent-health"])

_db = None
_hooks = {}
_run = {"fn": None, "running": False}


def init_torrent_health_router(db, switch_scene, run_once):
    global _db, _hooks
    _db = db
    _hooks = {"switch_scene": switch_scene}
    _run["fn"] = run_once


def _live_torrents(rows):
    """{(client id, hash): the client's dict} for these rows, one request per client; a client that does not answer contributes nothing."""
    out = {}
    for client_id in {r["client_id"] for r in rows}:
        client = torrent_health._client_of(_db, client_id)
        try:
            for t in (client.get_torrents(category="") if client else []):
                out[(client_id, str(t.get("hash") or "").lower())] = t
        except Exception:
            continue
    return out


def _public(r, now, live=None):
    cfg = torrent_health.load_cfg(_db)
    view = torrent_health.live_view(r, (live or {}).get((r["client_id"], str(r["job_id"]).lower())), now, cfg)
    if view:  # the client's numbers of this moment (display only; the monitor decides at its looks)
        r = {**r, **{k: view[k] for k in ("state", "detail", "seeds", "peers", "availability", "raw_state")}, "progress": view["progress"]}
    else:
        r = {**r, "detail": torrent_health.live_detail(r, now, cfg, torrent_health.check_interval_s(_db))}
    stalled = r.get("stalled_s") if r.get("stalled_s") else None  # observed stall time (see torrent_health.classify)
    return {
        "client_id": r["client_id"], "job_id": r["job_id"], "kind": r["kind"], "provider": r["provider"], "scene_id": r["scene_id"],
        "film_id": r["film_id"], "title": r["title"], "state": r["state"], "detail": r["detail"],
        "percent": round((r["progress"] or 0) * 100, 1), "max_percent": round((r["max_progress"] or 0) * 100, 1),
        "seeds": r["seeds"], "peers": r["peers"], "availability": r["availability"], "raw_state": r["raw_state"],
        "stalled_for": int(stalled) if stalled is not None else None, "checked_ago": int(now - r["last_checked_at"]),
    }


@router.get("")
def list_health(all: bool = False):
    """The torrents that need a look (stalled, stuck, error, missing); `all=true` lists every watched torrent."""
    now = time.time()
    rows = torrent_health.list_rows(_db, None if all else torrent_health.PROBLEM_STATES)
    live = _live_torrents(rows)
    return {"rows": [_public(r, now, live) for r in rows]}


@router.post("/check-now")
def check_now():
    """One monitor round right now, in the background (a search for an alternative can take a while)."""
    if _run["running"]:
        return {"started": False, "reason": "a check is already running"}
    def work():
        _run["running"] = True
        try:
            _run["fn"]()
        finally:
            _run["running"] = False
    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


@router.post("/switch")
def switch(body: dict):
    """"Find another": looks for another torrent now, whatever the progress (the user decided); the old one is removed when a replacement was
    grabbed. Scenes only -- a movie is removed and searched again from its wishlist entry."""
    row = torrent_health.get_row(_db, body.get("client_id"), str(body.get("job_id") or ""))
    if row is None:
        return {"error": "this torrent is not on the monitor's list (any more)"}
    if row["kind"] != "scene":
        return {"error": "for a movie use Remove: it goes back to the wishlist and its search proposes another release"}
    target = _target_of(row)
    if target is None:
        return {"error": "the download is no longer running"}
    found = _hooks["switch_scene"](target)
    if found == "failed":  # not a replacement: the old torrent stays
        return {"switched": False, "message": "the search did not work (the indexer did not answer) -- try again"}
    if not found:
        return {"switched": False, "message": "no other torrent found"}
    torrent_health.finish_replacement(_db, row)
    return {"switched": True}


def _target_of(row):
    for t in torrent_health.targets(_db):
        if str(t["job_id"]).lower() == row["job_id"] and t["client_id"] == row["client_id"]:
            return t
    return None


@router.post("/remove")
def remove(body: dict):
    return torrent_health.abandon(_db, body.get("client_id"), str(body.get("job_id") or ""))


@router.post("/readd")
def readd(body: dict):
    return torrent_health.readd(_db, body.get("client_id"), str(body.get("job_id") or ""))


@router.post("/keep")
def keep(body: dict):
    """"Keep waiting": the monitor flags the torrent but does not switch it for a day."""
    torrent_health.keep_waiting(_db, body.get("client_id"), str(body.get("job_id") or ""))
    return {"ok": True}
