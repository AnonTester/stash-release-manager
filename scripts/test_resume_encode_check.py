"""A restart while the sidecar checks a worker's finished encode: the check is resumed, the encode is NOT queued a second time (v0.260.13)."""
import os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import file_importer as fi, encode_queue, history  # noqa: E402
from db import Database  # noqa: E402

fails = []
def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)

db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))
fi._ensure_encode_thread = lambda: None  # no real encode thread in the test
d = tempfile.mkdtemp()
src = os.path.join(d, "dl", "x.mp4"); os.makedirs(os.path.dirname(src)); open(src, "wb").write(b"a" * 10)
INFO = {"width": 3840, "height": 2160, "duration": 100.0}
dst = fi.remote_output_path(src, "S1", INFO); open(dst, "wb").write(b"b" * 5)
calls = []
fi._reencode_plan = lambda db, provider, sid, f: {"info": INFO}
fi._verify_remote_result = lambda *a: calls.append(a)
history.scene = lambda *a, **k: calls.append(("history", a[2]))

# 1. no record: the encode is queued as before
r = fi._maybe_reencode(db, "stashdb", "S1", src)
check("no record: queued for a (new) encode", r == {"status": "waiting_encode"} and encode_queue.get("stashdb", "S1")["claimed_by"] is None and not [c for c in calls if c[0] != "history"])
encode_queue.remove("stashdb", "S1")

# 2. record written when the worker reported -> a restart (empty queue) resumes the check
fi._write_pending_verify("S1", src, dst, "Manji", 1200, False, "h265")
check("record is on disk", os.path.exists(fi._pending_verify_path("S1")))
r = fi._maybe_reencode(db, "stashdb", "S1", src)
time.sleep(0.2)
job = encode_queue.get("stashdb", "S1")
check("after the restart: the check is resumed, job is held for the check", r == {"status": "waiting_encode"} and job["claimed_by"] == "verify")
check("the check thread got the worker's data", any(c[:4] == ("stashdb", "S1", "Manji", 1200) for c in calls if c and c[0] != "history"))
check("History says so", any(c[0] == "history" and "not encoded a second time" in c[1] for c in calls))
encode_queue.remove("stashdb", "S1")

# 3. a record whose encoded file is gone is dropped -> normal queue
os.remove(dst)
calls.clear()
r = fi._maybe_reencode(db, "stashdb", "S1", src)
check("encoded file gone: record dropped, queued for a new encode", encode_queue.get("stashdb", "S1")["claimed_by"] is None
      and not os.path.exists(fi._pending_verify_path("S1")))
encode_queue.remove("stashdb", "S1")

# 4. the record of ANOTHER download is not used
open(dst, "wb").write(b"b")
fi._write_pending_verify("S1", src + ".other", dst, "Manji", 1, False, "h265")
calls.clear()
fi._maybe_reencode(db, "stashdb", "S1", src)
check("record of another download is ignored", encode_queue.get("stashdb", "S1")["claimed_by"] is None)
encode_queue.remove("stashdb", "S1")

# 5. the check's end removes the record
fi._write_pending_verify("S2", src, dst, "Manji", 1, False, "h265")
fi._drop_pending_verify("S2")
check("drop removes the record", not os.path.exists(fi._pending_verify_path("S2")))
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
