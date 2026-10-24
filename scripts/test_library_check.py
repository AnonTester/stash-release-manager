"""Tests (v0.237.2): the one-time library check -- wrong-length files become candidates (not twice, not over an existing candidate), the candidate
refresh runs, and the VR rule is a file size. Run:  venv/bin/python scripts/test_library_check.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import duration_check  # noqa: E402
import upgrade_library_check as ulc  # noqa: E402
import upgrades  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))

def save(sid, file_sec, provider_sec, path):
    duration_check._save(db, {"id": sid, "title": f"S{sid}"}, {"path": path, "size": 1000, "duration": file_sec}, "stashdb", f"p{sid}", provider_sec)
save(1, 600, 1800, "/m/1.mp4")      # far too short
save(2, 1800, 1800, "/m/2.mp4")     # fine
save(3, 3000, 1800, "/m/3.mp4")     # far too long
save(4, 600, 1800, "/m/4.mp4")      # too short, but already a candidate
with db.connect() as conn:
    conn.execute("""INSERT INTO upgrade_candidate (scene_id, file_path, scene_title, provider, provider_scene_id, reason, status)
                    VALUES ('4', '/m/4.mp4', 'S4', 'stashdb', 'p4', 'vr_low_quality', 'wanted')""")

class Stash:
    def _graphql(self, q, v):
        sid = v["ids"][0]
        return {"findScenes": {"scenes": [{"id": str(sid), "title": f"S{sid}", "tags": [],
                                           "files": [{"path": f"/m/{sid}.mp4", "width": 1920, "height": 1080, "size": 1000, "duration": 600}],
                                           "stash_ids": [{"endpoint": "https://stashdb.org/graphql", "stash_id": f"p{sid}"}]}]}}
    stashdb_endpoint = "https://stashdb.org/graphql"
    theporndb_endpoint = "https://theporndb.net"

class DJob:
    def __init__(self): self.status = {"running": False, "phase": "done"}; self.started = 0
    def start(self, force=False): self.started += 1; return {"started": True}

refreshed = []
_orig_refresh = upgrades.refresh_candidates
upgrades.refresh_candidates = lambda db_, sc, get, cutoff: refreshed.append(cutoff) or {"vr_2d": 1, "vr_mono": 0, "vr_low_quality": 2, "below_cutoff": 3}
job = ulc.UpgradeLibraryCheck(db, lambda: Stash(), DJob(), lambda k, d: d, lambda: 720)
r = job.run()
check("the run finishes", r == {"ok": True} and job.status["phase"] == "done" and not job.status["running"])
with db.connect() as conn:
    rows = {r["scene_id"]: r["reason"] for r in conn.execute("SELECT scene_id, reason FROM upgrade_candidate")}
with db.connect() as conn:
    states = {r["scene_id"]: r["status"] for r in conn.execute("SELECT scene_id, status FROM upgrade_candidate")}
check("wrong-length files became 'duration' candidates", rows.get("1") == "duration" and rows.get("3") == "duration")
with db.connect() as conn:
    cids = {r["scene_id"]: r["id"] for r in conn.execute("SELECT id, scene_id FROM upgrade_candidate")}
check("they wait for a look ('check'), the automatic search does not take them", states["1"] == "check" and states["3"] == "check"
      and not set(upgrades.due_candidates(db, 50, min_hours=0)) & {cids["1"], cids["3"]})
check("a fine file did not", "2" not in rows)
check("a file that already is a candidate keeps its reason", rows.get("4") == "vr_low_quality")
check("the candidate refresh ran with the profile's cutoff", refreshed == [720] and job.status["counts"]["below_cutoff"] == 3 and job.status["added_duration"] == 2)
job.run()
with db.connect() as conn:
    n = conn.execute("SELECT COUNT(*) FROM upgrade_candidate WHERE reason = 'duration'").fetchone()[0]
check("a second run adds nothing twice", n == 2)
check("another run while one is going is refused", (lambda j: (j.status.update(running=True), j.start())[1])(job)["skipped"] is True)

# a scene that is gone from Stash must not stop the run
save(8, 600, 1800, "/m/8.mp4")
_orig_add = upgrades.add_manual_candidate
def _add(db_, sc, sid, reason, psec=None):
    if str(sid) == "8":
        raise RuntimeError("Stash GraphQL error: scene with id 8 not found")
    return _orig_add(db_, sc, sid, reason, psec)
upgrades.add_manual_candidate = _add
job3 = ulc.UpgradeLibraryCheck(db, lambda: Stash(), DJob(), lambda k, d: d, lambda: 720)
r3 = job3.run()
check("a vanished scene is skipped, the run still finishes", r3 == {"ok": True} and job3.status["phase"] == "done" and job3.status["skipped_duration"] == 1)
upgrades.add_manual_candidate = _orig_add

# the real failure of the first runs: the VR check's table still names a scene that is gone from Stash
class Gone:
    calls = []
    def _graphql(self, q, v):
        Gone.calls.append(list(v["ids"]))
        if "6922" in v["ids"] or 6922 in v["ids"]:
            raise RuntimeError("Stash GraphQL error: scene with id 6922 not found")
        return {"findScenes": {"scenes": [{"id": i} for i in v["ids"]]}}
got = upgrades._scenes_by_ids(Gone(), ["1", "6922", "3"])
check("a vanished scene id is skipped, the others come back", [s["id"] for s in got] == ["1", "3"])

# the result of the last run is kept (a new page view shows it without a new check)
kept = job.last_run()
check("the last result is kept in the settings", kept and kept["added_duration"] == job.status["added_duration"] and kept["counts"]["below_cutoff"] == 3 and kept["cutoff"] == 720)
job2 = ulc.UpgradeLibraryCheck(db, lambda: Stash(), DJob(), lambda k, d: d, lambda: 720)
check("a fresh job object (restart) still finds it", job2.last_run()["finished_at"] == kept["finished_at"])

# the VR rule: a bitrate, not a width or a size
import inspect  # noqa: E402
src = inspect.getsource(_orig_refresh)
check("VR low quality is a bitrate rule (no width, no size)", "vr_upgrade_min_mbps" in src and "min_width" not in src and "min_bytes" not in src)
check("scenes in groups are left out of the below-cutoff query", "groups: {modifier: IS_NULL}" in upgrades._LOW_RES_QUERY)

print("\nALL OK" if not FAILS else "\nFAILED: %s" % FAILS)
sys.exit(1 if FAILS else 0)
