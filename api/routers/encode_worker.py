"""Endpoints of the re-encode worker (v0.220.0): a second machine (worker/srm_encode_worker.py) asks for queued 2160p encodes, reports
progress and the result. Pull only -- a worker that is off just leaves the job waiting. The LAN has no login here, like the rest of the sidecar."""
import os
import logging
from fastapi import APIRouter, HTTPException
import encode_queue
import file_importer
import import_progress
from routers.settings import get_setting_value

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/encode-worker", tags=["encode-worker"])
_db = None


def init_encode_worker_router(db):
    global _db
    _db = db


def wants_worker(db):
    return get_setting_value(db, "encode_target", "nas") == "worker"


@router.get("/next")
def next_job(worker: str = "worker"):
    """The oldest waiting encode, claimed for `worker`, or {"job": null}. Paths are the sidecar's own (/downloads/...): the worker maps them."""
    worker = (worker or "worker")[:40]
    encode_queue.mark_worker_seen(worker)
    if not wants_worker(_db):
        return {"job": None, "reason": "encode_target is not 'worker'"}
    job = encode_queue.claim_remote(worker)
    if job is None:
        return {"job": None}
    info = job["info"]
    try:
        quality = int(get_setting_value(_db, "transcode_quality", "24") or 24)
    except ValueError:
        quality = 24
    codec = "h264" if get_setting_value(_db, "transcode_codec", "h265") == "h264" and not info.get("hdr") else "h265"
    import_progress.set_phase(job["provider"], job["stash_id"], "encoding", percentage=0, method=f"{worker}: starting")
    return {"job": {
        "provider": job["provider"], "stash_id": job["stash_id"], "source": job["source"],
        "dest": file_importer.remote_output_path(job["source"], job["stash_id"], info), "info": info,
        "quality": quality, "codec": codec, "preset": get_setting_value(_db, "transcode_preset", "fast"),
        "hw_mode": get_setting_value(_db, "transcode_hw", "auto"),
    }}


@router.post("/progress")
def progress(body: dict):
    provider, stash_id, worker = body.get("provider"), body.get("stash_id"), body.get("worker")
    encode_queue.mark_worker_seen(worker)
    if not encode_queue.heartbeat(provider, stash_id, worker):
        return {"ok": False}  # the job is gone or went to someone else: the worker stops
    import_progress.set_phase(provider, stash_id, "encoding", percentage=body.get("percentage") or 0, speed_x=body.get("speed_x"),
                              seconds_left=body.get("seconds_left"), method=f"{worker}: {body.get('method') or ''}".strip(": "))
    return {"ok": True}


@router.post("/done")
def done(body: dict):
    result = file_importer.finish_remote_encode(
        body.get("provider"), body.get("stash_id"), body.get("worker"), bool(body.get("ok")), error=body.get("error"),
        seconds=body.get("seconds"), hw=bool(body.get("hw")), codec=body.get("codec") or "h265")
    if result.get("error"):
        raise HTTPException(409, result["error"])
    return result


@router.post("/run-local")
def run_local(body: dict):
    """"Encode on the NAS now" for a job that waits for a worker."""
    if not encode_queue.run_local_now(body.get("provider"), body.get("scene_stashdb_id")):
        raise HTTPException(404, "no waiting encode for this scene")
    return {"ok": True}


@router.get("/status")
def status():
    return {**encode_queue.worker_status(), "wanted": wants_worker(_db)}
