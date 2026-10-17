"""Tests of "Accept as one scene" (v0.221.0): film_import.finalize with film.as_scene against a THROWAWAY database and a fake Stash / movies helper.
Run:  venv/bin/python scripts/test_film_part.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import film_import  # noqa: E402
import film_repo  # noqa: E402
import file_importer  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)


file_importer._trigger_generate = lambda stash, sid: None
class Stash:  # noqa: E301
    def find_scene_by_path(self, p): return {"id": "s" + os.path.basename(p), "duration": 600}
class Movies:  # noqa: E301
    def __init__(self): self.calls = []
    def create_group(self, title, ids, group_id=None):
        self.calls.append((title, list(ids), group_id))
        return {"group_id": group_id or "G-new"}
film_import._scene_for_path = lambda st, p: st.find_scene_by_path(p)

db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
movies = Movies()
# the film has an expected length of 120 min; the 10 min part would fail the length check without as_scene
fid, _ = film_repo.add(db, title="Me First", year=2011, tpdb_id="t1", duration_min=120)
film_repo.update(db, fid, status="grabbed", grabbed_title="Me.First_4", as_scene=1, download_job_id="j", download_client_id=1,
                 pending_import_path=json.dumps(["/data/Movies/Me First (2011)/p4.mp4"]))
out = film_import.finalize(db, Stash(), film_repo.get(db, fid, with_json=True), movies, None)
f = film_repo.get(db, fid, with_json=True)
check("as_scene skips the length check and reports part_imported", out["status"] == "part_imported")
check("a new Group was made from the title, the scene joined", movies.calls == [("Me First", ["sp4.mp4"], None)])
check("movie stays wanted, Group remembered, part counted", f["status"] == "wanted" and f["stash_group_id"] == "G-new" and f["parts_imported"] == 1)
check("job/candidate cleared, release rejected, flag reset", f["download_job_id"] is None and f["pending_import_path"] is None and f["as_scene"] == 0
      and f["rejected_titles"] == ["Me.First_4"] and f["review_candidate"] is None)

# the next part joins the SAME group
film_repo.update(db, fid, status="grabbed", grabbed_title="Me.First_5", as_scene=1, pending_import_path=json.dumps(["/data/Movies/Me First (2011)/p5.mp4"]))
film_import.finalize(db, Stash(), film_repo.get(db, fid, with_json=True), movies, None)
f = film_repo.get(db, fid, with_json=True)
check("second part joins the same Group", movies.calls[-1] == (None, ["sp5.mp4"], "G-new") and f["parts_imported"] == 2)
check("both releases are rejected", f["rejected_titles"] == ["Me.First_4", "Me.First_5"])

# without as_scene the length check still applies
g, _ = film_repo.add(db, title="Whole", year=2012, tpdb_id="t2", duration_min=120)
film_repo.update(db, g, status="grabbed", pending_import_path=json.dumps(["/data/Movies/W/w.mp4"]))
out = film_import.finalize(db, Stash(), film_repo.get(db, g, with_json=True), movies, None)
check("a normal grab still fails a wrong length", out["status"] == "import_failed" and film_repo.get(db, g)["status"] == "import_failed")

# a remembered Group that was deleted in Stash meanwhile (Me First: group 390) -> a new Group is made instead of an error
class GoneMovies(Movies):
    def create_group(self, title, ids, group_id=None):
        if group_id == "390":
            raise ValueError("group not found")
        return super().create_group(title, ids, group_id)
gm = GoneMovies()
h, _ = film_repo.add(db, title="Me First 2", year=2011, tpdb_id="t3", duration_min=120)
film_repo.update(db, h, status="grabbed", stash_group_id="390", as_scene=1, pending_import_path=json.dumps(["/data/Movies/M/p1.mp4"]))
out = film_import.finalize(db, Stash(), film_repo.get(db, h, with_json=True), gm, None)
check("a deleted remembered Group -> new Group, stash_group_id replaced", out["status"] == "part_imported" and film_repo.get(db, h)["stash_group_id"] == "G-new")

# the sweep: an empty-group movie with a dead Group is dismissed, another one forgets the id, a living Group stays, an empty answer changes nothing
class SweepStash:
    def __init__(self, ids): self.ids = ids
    def _graphql(self, q, v): return {"findGroups": {"groups": [{"id": i} for i in self.ids]}}
e1, _ = film_repo.add(db, title="Empty One", year=2001, tpdb_id="e1"); film_repo.update(db, e1, status="wanted", source="empty-group", stash_group_id="901")
e2, _ = film_repo.add(db, title="Other One", year=2002, tpdb_id="e2"); film_repo.update(db, e2, status="wanted", source="tpdb", stash_group_id="902")
e3, _ = film_repo.add(db, title="Alive One", year=2003, tpdb_id="e3"); film_repo.update(db, e3, status="wanted", source="empty-group", stash_group_id="903")
check("an empty Stash answer changes nothing", film_import.forget_dead_groups(db, SweepStash([])) == [] and film_repo.get(db, e1)["status"] == "wanted")
dry = film_import.forget_dead_groups(db, SweepStash(["903"]), dry_run=True)
check("dry run lists but changes nothing", {d["id"]: d["action"] for d in dry if d["id"] in (e1, e2, e3)} == {e1: "dismiss", e2: "forget_group"} and film_repo.get(db, e1)["status"] == "wanted")
film_import.forget_dead_groups(db, SweepStash(["903"]))
check("dead empty-group movie dismissed, other forgets the id, living Group untouched",
      film_repo.get(db, e1)["status"] == "dismissed" and film_repo.get(db, e2)["status"] == "wanted" and not film_repo.get(db, e2)["stash_group_id"]
      and film_repo.get(db, e3)["stash_group_id"] == "903" and film_repo.get(db, e3)["status"] == "wanted")

print("\n%s" % ("ALL OK" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
