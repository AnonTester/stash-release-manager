"""Tests (v0.232.3): an encode worker survives a sidecar restart. The queue is in memory: after a restart it is empty until the import run
queues the waiting downloads again (unclaimed). The worker that is still encoding must not be told "the job is gone", gets its job back
when it is queued again, and reports the result with retries. No ffmpeg.   Run:  venv/bin/python scripts/test_encode_restart.py"""
import os
import sys
import tempfile
import time
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import encode_queue as eq  # noqa: E402
import srm_encode_worker as w  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

eq.clear()
# --- right after the (simulated) restart: the queue is empty
eq._started_at = time.time()
check("empty queue within the grace time: the worker keeps going", eq.heartbeat("stashdb", "s1", "Manji") is True)
eq._started_at = time.time() - eq.RESTART_GRACE_SECONDS - 5
check("empty queue long after the start: the job is really gone", eq.heartbeat("stashdb", "s1", "Manji") is False)

# --- the import run queued it again (unclaimed): it goes back to the worker that is encoding it
eq.add(None, "stashdb", "s1", "/downloads/x.mkv", {})
check("an unclaimed job is handed back to the heartbeating worker", eq.heartbeat("stashdb", "s1", "Manji") is True and eq.get("stashdb", "s1")["claimed_by"] == "Manji")
check("... and stays its own", eq.heartbeat("stashdb", "s1", "Manji") is True)
check("another worker cannot beat on it", eq.heartbeat("stashdb", "s1", "Other") is False)

eq.add(None, "stashdb", "s2", "/downloads/y.mkv", {})
eq.run_local_now("stashdb", "s2")
check("a job the user sent to the NAS is not handed to a worker", eq.heartbeat("stashdb", "s2", "Manji") is False and eq.get("stashdb", "s2")["claimed_by"] is None)

eq.add(None, "stashdb", "s3", "/downloads/z.mkv", {})
check("adopt: an unclaimed job", eq.adopt("stashdb", "s3", "Manji") is True and eq.get("stashdb", "s3")["claimed_by"] == "Manji")
check("adopt: not a claimed one, not an unknown one", eq.adopt("stashdb", "s3", "Other") is False and eq.adopt("stashdb", "nope", "Manji") is False)

# --- the worker: the final report is retried, a progress report that fails does not stop the encode
calls, sleeps = [], []
def flaky(base, path, body=None, timeout=30):
    calls.append(path)
    if len(calls) < 3:
        raise urllib.error.URLError("connection refused")
    return {"ok": True}
w.call, w.time.sleep = flaky, lambda s: sleeps.append(s)
check("call_retry gets through after two failures", w.call_retry("http://x", "/encode-worker/done", {}, tries=5, wait=1) == {"ok": True} and len(calls) == 3 and sleeps == [1, 1])
calls.clear(); sleeps.clear()
def dead(base, path, body=None, timeout=30):
    calls.append(path); raise urllib.error.URLError("down")
w.call = dead
try:
    w.call_retry("http://x", "/p", {}, tries=3, wait=1); raised = False  # noqa: E702
except urllib.error.URLError: raised = True  # noqa: E701
check("call_retry gives up after its tries", raised and len(calls) == 3)

eq.clear()
print("\nFAILED: " + ", ".join(FAILS) if FAILS else "\nall ok")
sys.exit(1 if FAILS else 0)
