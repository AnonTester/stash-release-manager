"""Queue of re-encode jobs (v0.219.0, workers v0.220.0).

A 2160p last-resort download is scaled down to 1080p before the import (transcode.py). The job waits here until someone picks it up: the
sidecar's own thread (file_importer._encode_worker) or, when the setting `encode_target` is "worker", an encode worker on a faster machine
(worker/srm_encode_worker.py, talking to routers/encode_worker.py). In memory only: a restart loses the queue, the next import run
queues every waiting download again.
"""
import threading
import time

import import_progress

LEASE_SECONDS = 600  # a claimed job without a sign of life for this long goes back to the queue
WORKER_SEEN_SECONDS = 180

_lock = threading.Lock()
_jobs = {}           # (provider, stash_id) -> job dict, in the order they were queued
_last_worker_seen = {"name": None, "at": 0.0}


def _key(provider, stash_id):
    return (provider or "stashdb", stash_id)


def add(db, provider, stash_id, source_file, info):
    with _lock:
        _jobs[_key(provider, stash_id)] = {
            "db": db, "provider": provider, "stash_id": stash_id, "source": source_file, "info": info,
            "queued_at": time.time(), "claimed_by": None, "claimed_at": 0.0, "beat": 0.0, "local": False,
        }


def active(provider, stash_id):
    with _lock:
        return _key(provider, stash_id) in _jobs


def remove(provider, stash_id):
    with _lock:
        _jobs.pop(_key(provider, stash_id), None)


def get(provider, stash_id):
    with _lock:
        return _jobs.get(_key(provider, stash_id))


def _expire_locked(now):
    for job in _jobs.values():
        if job["claimed_by"] and job["claimed_by"] != "local" and now - job["beat"] > LEASE_SECONDS:
            job["claimed_by"] = None
            import_progress.set_phase(job["provider"], job["stash_id"], "waiting_encode", can_local=True)  # the worker went silent


def mark_worker_seen(name):
    with _lock:
        _last_worker_seen.update(name=name, at=time.time())


def worker_status():
    """{"name", "seconds_ago", "online"} of the last encode worker that asked for work."""
    with _lock:
        at = _last_worker_seen["at"]
        return {"name": _last_worker_seen["name"], "seconds_ago": int(time.time() - at) if at else None,
                "online": bool(at) and time.time() - at < WORKER_SEEN_SECONDS}


def claim_remote(worker):
    """The oldest unclaimed job for an encode worker (marked as claimed), or None."""
    now = time.time()
    with _lock:
        _expire_locked(now)
        for job in _jobs.values():
            if not job["claimed_by"] and not job["local"]:
                job.update(claimed_by=worker, claimed_at=now, beat=now)
                return dict(job)
    return None


def claim_local(wants_worker, wait_hours):
    """The oldest unclaimed job the sidecar may encode itself: all of them when no worker is wanted, else only those the user sent to the
    NAS ("Encode on the NAS now") or that waited longer than `wait_hours` (0 = never)."""
    now = time.time()
    with _lock:
        _expire_locked(now)
        for job in _jobs.values():
            if job["claimed_by"]:
                continue
            if (not wants_worker or job["local"] or (wait_hours and now - job["queued_at"] >= wait_hours * 3600)):
                job.update(claimed_by="local", claimed_at=now, beat=now)
                return dict(job)
    return None


def run_local_now(provider, stash_id):
    """"Encode on the NAS now": the job may be taken by the sidecar's own thread. False when there is no unclaimed job."""
    with _lock:
        job = _jobs.get(_key(provider, stash_id))
        if not job or (job["claimed_by"] and job["claimed_by"] != "local"):
            return False
        job["local"] = True
        return True


def heartbeat(provider, stash_id, worker):
    """True while `worker` still holds this job (and renews the lease)."""
    with _lock:
        job = _jobs.get(_key(provider, stash_id))
        if not job or job["claimed_by"] != worker:
            return False
        job["beat"] = time.time()
        return True


def waiting_for_worker(provider, stash_id, wants_worker):
    """True for a queued, unclaimed job that a worker is expected to take (drives the "Encode on the NAS now" button)."""
    with _lock:
        job = _jobs.get(_key(provider, stash_id))
        return bool(wants_worker and job and not job["claimed_by"] and not job["local"])


def clear():
    with _lock:
        _jobs.clear()
