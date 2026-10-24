"""Tests (v0.238.0): "Re-check fingerprints" on a Library-check finding (Needs manual review) -- a match closes it, one other scene makes it a
correction, several an ambiguous finding, nothing keeps it unverifiable. Run:  venv/bin/python scripts/test_finding_recheck.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import library_check  # noqa: E402
import library_check_repo as repo  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))
EP = "https://stashdb.org/graphql"
class Stash:
    stashdb_endpoint = EP
    def scene_for_fingerprint_check(self, sid):
        if str(sid) == "404": return None
        return {"id": str(sid), "title": f"S{sid}", "stash_ids": [{"endpoint": EP, "stash_id": "mine"}],
                "files": [{"path": f"/m/{sid}.mp4", "fingerprints": [{"type": "phash", "value": "ab"}]}]}
class Stashdb:
    def __init__(self, found): self.found = found
    def find_scenes_by_fingerprints(self, fps): return [self.found for _ in fps]

def finding(kind, sid="1", reason=None):
    run = repo.create_run(db, [])
    repo.insert_finding(db, run, sid, f"S{sid}", kind, expected_stashdb_id="mine" if kind != "unverifiable" else None,
                        candidates=[{"id": "x", "title": "X"}] if kind == "ambiguous" else None, file_paths=[f"/m/{sid}.mp4"], reason=reason)
    return repo.list_findings(db, "pending")[0] if False else [f for f in repo.list_findings(db, "pending") if f["scene_id"] == sid][0]

f = finding("unverifiable", "1", "StashDB has no fingerprint match at all for this file")
r = library_check.recheck_finding(db, Stash(), Stashdb([{"id": "mine", "title": "Mine"}]), f)
check("a match closes the finding", r["result"] == "verified" and repo.get_finding(db, f["id"]) is None)

f = finding("unverifiable", "2", "no match")
r = library_check.recheck_finding(db, Stash(), Stashdb([{"id": "other", "title": "Other Scene"}]), f)
now = [x for x in repo.list_findings(db, "pending") if x["scene_id"] == "2"]
check("one other scene: it becomes a correction", r["result"] == "mismatch" and "Other Scene" in r["message"] and len(now) == 1 and now[0]["kind"] == "mismatch")

f = finding("unverifiable", "3", "no match")
r = library_check.recheck_finding(db, Stash(), Stashdb([{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]), f)
now = [x for x in repo.list_findings(db, "pending") if x["scene_id"] == "3"]
check("several scenes: ambiguous, with the candidates", r["result"] == "ambiguous" and now[0]["kind"] == "ambiguous" and len(now[0]["candidates"]) == 2)

f = finding("unverifiable", "4", "no match")
r = library_check.recheck_finding(db, Stash(), Stashdb([]), f)
check("still nothing: unverifiable, the finding stays", r["result"] == "unverifiable" and repo.get_finding(db, f["id"]) is not None)

f = finding("unverifiable", "404", "no match")
r = library_check.recheck_finding(db, Stash(), Stashdb([]), f)
check("a scene that is gone from Stash gives an error", "error" in r)

print("\nALL OK" if not FAILS else "\nFAILED: %s" % FAILS)
sys.exit(1 if FAILS else 0)
