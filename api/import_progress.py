"""Live import phases (2026-09-26, "import progress on the Activity page").

After a download finishes, the import used to run invisibly: the file
copy into the library alone takes ~10 min for a 20 GB VR file (hardlinks
fail across the two mounts), then Stash scans and the scene gets
verified/tagged. This in-memory registry holds the TRANSIENT phases the
database doesn't know about, per scene:

- 'waiting_import' -- the download client reported the job finished
  (SABnzbd hook), the import run hasn't picked it up yet;
- 'preparing'      -- import_one() started (metadata lookup, naming);
- 'copying'        -- the file copy, with bytes done/total and speed.

The later phases come from the database instead and survive a restart
(see routers/releases.py's download_progress()): `pending_import_path`
set = moved, Stash scan + verification pending ('verifying').

In memory only on purpose: a restart mid-copy restarts that import
anyway, so a stale "copying 43 %" must not survive it.
"""
import threading
import time

import events

_lock = threading.Lock()
_state = {}  # (provider, scene_stashdb_id) -> dict


def set_phase(provider, scene_stashdb_id, phase, **fields):
    with _lock:
        entry = {"phase": phase, "since": time.time()}
        entry.update(fields)
        _state[(provider or "stashdb", scene_stashdb_id)] = entry
    events.wake()


def update_copy(provider, scene_stashdb_id, bytes_done, bytes_total, started_at):
    elapsed = max(time.time() - started_at, 0.001)
    speed = bytes_done / elapsed
    remaining = (bytes_total - bytes_done) / speed if speed > 0 else None
    with _lock:
        _state[(provider or "stashdb", scene_stashdb_id)] = {
            "phase": "copying", "since": started_at,
            "bytes_done": bytes_done, "bytes_total": bytes_total,
            "percentage": int(bytes_done * 100 / bytes_total) if bytes_total else 100,
            "speed_bps": int(speed), "seconds_left": int(remaining) if remaining is not None else None,
        }
    events.wake()


def clear(provider, scene_stashdb_id):
    with _lock:
        _state.pop((provider or "stashdb", scene_stashdb_id), None)
    events.wake()


def snapshot():
    """{scene_stashdb_id: entry} -- keyed by scene only, like the
    download-progress endpoint (scene ids don't collide across
    providers in practice; the provider is kept inside for reference)."""
    with _lock:
        return {scene: {**entry, "provider": provider} for (provider, scene), entry in _state.items()}
