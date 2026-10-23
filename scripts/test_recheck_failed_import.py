"""Tests (v0.235.0): "Re-check fingerprints" on an Import-failed row -- a match imports, a miss only reports (fingerprint count, nearest phash).
Run:  venv/bin/python scripts/test_recheck_failed_import.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import file_importer as fi  # noqa: E402
import release_repo  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))
PATH = "/media/x/file.mp4"
with db.connect() as conn:
    release_repo.insert_release(db, conn, {"id": "s1", "title": "T", "date": "2026-01-01", "created": "2026-01-01", "performers": []},
                                "stashdb", "studio", "e1", status="import_failed")
release_repo.set_pending_import_path_for_scene(db, "stashdb", "s1", PATH)
release_repo.set_import_error_for_scene(db, "stashdb", "s1", "fingerprint_mismatch", "no match")

class Stash:
    def find_scene_by_path(self, path):
        return {"id": "9", "title": "", "files": [{"path": PATH, "fingerprints": [{"type": "phash", "value": "ff00"}]}]}
class Provider:
    def __init__(self, fps): self.fps = fps
    def get_scene_fingerprints(self, sid): return self.fps
fi._provider_client = lambda provider, stashdb, getter: stashdb

verdict = {}
fi.verify_downloaded_file = lambda sc, cl, sid, path, phash_tolerance=0: verdict["v"]
fi._finalize_verification = lambda db_, sc, cl, provider, sid, path: {"status": "imported"}

# 1. still mismatching: nearest phash 4 bits away is reported, nothing changes
verdict["v"] = (False, "")
r = fi.recheck_failed_import(db, Stash(), Provider([{"algorithm": "PHASH", "hash": "ff0f"}, {"algorithm": "OSHASH", "hash": "ab"}]), "stashdb", "s1")
check("still mismatch reports count and bits", r["result"] == "still_mismatch" and r["remote_fingerprints"] == 2 and r["closest_phash_bits"] == 4)
check("message names the bits and says only an exact match imports", "4 bits" in r["message"] and "exact match" in r["message"] and r["likely_same_video"] is True)
check("status unchanged", release_repo.list_rows_for_scene(db, "stashdb", "s1")[0]["status"] == "import_failed")

# 1b. far away: no 'likely the same video' hint
r = fi.recheck_failed_import(db, Stash(), Provider([{"algorithm": "PHASH", "hash": "00ff"}]), "stashdb", "s1")
check("far phash is no hint", r["likely_same_video"] is False and "File is correct" not in r["message"])

# 2. provider still has nothing
verdict["v"] = ("unverified", "")
r = fi.recheck_failed_import(db, Stash(), Provider([]), "stashdb", "s1")
check("no fingerprints", r["result"] == "no_fingerprints" and r["remote_fingerprints"] == 0)

# 3. not decidable
verdict["v"] = (None, None)
r = fi.recheck_failed_import(db, Stash(), Provider([{"algorithm": "PHASH", "hash": "ff00"}]), "stashdb", "s1")
check("unknown", r["result"] == "unknown")

# 4. match -> finalised through the normal path
verdict["v"] = (True, "T")
r = fi.recheck_failed_import(db, Stash(), Provider([{"algorithm": "PHASH", "hash": "ff00"}]), "stashdb", "s1")
check("match imports", r["result"] == "verified" and r["status"] == "imported")

# 5. a row without the failed state
release_repo.set_import_error_for_scene(db, "stashdb", "s1", None, None)
check("not a failed import -> error", "error" in fi.recheck_failed_import(db, Stash(), Provider([]), "stashdb", "s1"))

print("\nALL OK" if not FAILS else "\nFAILED: %s" % FAILS)
sys.exit(1 if FAILS else 0)
