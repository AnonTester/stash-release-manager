"""Tests (v0.232.1): the progress shape of a download that waits for its re-encode carries who is expected to encode (worker online or not)
and since when it waits -- constant values only, so the live push does not fire every few seconds.
Run:  venv/bin/python scripts/test_encode_wait_phase.py"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import encode_queue  # noqa: E402
from routers import releases  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

entry = {"phase": "waiting_encode", "since": time.time(), "can_local": True}
encode_queue.clear()
out = releases._import_phase(entry, "scene-1")
check("no worker ever seen: offline, no name", out["worker_online"] is False and "worker_name" not in out and out["can_local"] is True)
check("without a queued job the phase's own time is the fallback", isinstance(out["queued_at"], int) and out["queued_at"] > 0)

encode_queue.add(None, "stashdb", "scene-1", "/downloads/x.mkv", {})
queued = encode_queue.queued_at("scene-1")
out = releases._import_phase(entry, "scene-1")
check("the queue's own time is used", out["queued_at"] == int(queued))
check("queued_at of another scene is None", encode_queue.queued_at("other") is None)

encode_queue.mark_worker_seen("Manji")
out = releases._import_phase(entry, "scene-1")
check("a worker that just asked for work: online with its name", out["worker_online"] is True and out["worker_name"] == "Manji")
check("two calls give the same values (nothing that ticks)", releases._import_phase(entry, "scene-1") == out)

out = releases._import_phase({"phase": "copying", "percentage": 40, "bytes_done": 1, "bytes_total": 2}, "scene-1")
check("other phases are unchanged", out == {"phase": "copying", "percentage": 40, "bytes_done": 1, "bytes_total": 2})

encode_queue.clear()
print("\nFAILED: " + ", ".join(FAILS) if FAILS else "\nall ok")
sys.exit(1 if FAILS else 0)
