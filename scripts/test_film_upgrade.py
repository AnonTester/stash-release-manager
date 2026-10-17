"""Movie upgrade search (v0.222.0): judging, due list, search, the import that replaces the owned file, and the way back on every failure.
THROWAWAY database, fake Stash. Run:  venv/bin/python scripts/test_film_upgrade.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import download_jobs  # noqa: E402
import file_importer  # noqa: E402
import film_import  # noqa: E402
import film_repo  # noqa: E402
import film_search as fs  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

file_importer._trigger_generate = lambda stash, sid: None

# ---- 1. judging
film = {"title": "Example Film 2", "year": 2000, "duration_min": 83, "studio_name": "Some Studio", "performers": []}
def cand(name, gb=2.0): return {"title": name, "size": int(gb * 1e9)}  # noqa: E302
j = lambda name, owned=416, minr=720: fs.judge_upgrade(film, cand(name), minr, owned)[0]  # noqa: E731
check("1080p beats an owned 480p file", j("Example.Film.2.2000.1080p.WEB-DL"))
check("720p beats an owned 480p file when 720p is the minimum", j("Example.Film.2.2000.720p.WEB-DL"))
check("a 480p release is not better than the owned 480p file", not j("Example.Film.2.2000.480p.DVDRip"))
check("a release below the profile's minimum is refused", not j("Example.Film.2.2000.576p.DVDRip"))
check("a name without a resolution is refused", not j("Example.Film.2.2000.WEB-DL"))
check("the same class as the owned file is refused (720p owned)", not j("Example.Film.2.2000.720p.WEB-DL", owned=720))
check("a plain title mismatch is still refused", not j("Other.Movie.1080p"))
check("resolution classes: 416 -> 480, 1080 -> 1080, 700 -> 720, and a letterboxed 1920x800 counts as 1080p",
      fs.resolution_class(416) == 480 and fs.resolution_class(1080) == 1080 and fs.resolution_class(700) == 720 and fs.resolution_class(800, 1920) == 1080)

# a single scene of the movie is no upgrade of the movie: found live 2026-10-17 ("StudioName.11.06.28.Jane.D.Joes.Movie.13.Sc4...720p", 1.8 GB for a 217 min movie)
joe = {"title": "Joe's Movie 13", "year": 2011, "duration_min": 217, "studio_name": "Some Studio", "performers": []}
check("a scene release with a yy.mm.dd date and 'Sc4' is refused", not fs.judge(joe, {"title": "StudioName.11.06.28.Jane.D.Joes.Movie.13.Sc4.[AudioDelayFix].720p", "size": 1.79e9})[0])
check("'Scene 2' in the name is refused", not fs.judge(joe, {"title": "Joes.Movie.13.Scene.2.720p", "size": 2e9})[0])
check("a plain movie release is still taken", fs.judge(film, {"title": "Example.Film.2.2000.720p.WEB-DL", "size": 2e9})[0])

# an apostrophe in the title: releases write "Joe's" as Joes or Joe.s
check("Joe.s.Movie.13 and Joes.Movie.13 both name \"Joe's Movie 13\"", fs.judge(joe, {"title": "Joe.s.Movie.13.2011.720p.WEB-DL", "size": 4e9})[0] and fs.judge(joe, {"title": "Joes.Movie.13.720p.XXX", "size": 4e9})[0])
check("a different volume of the same series is still refused", not fs.judge(joe, {"title": "Joe.s.Movie.14.720p", "size": 4e9})[0])
check("the apostrophe query without the apostrophe is asked last, only for such titles", fs.queries(joe)[-1] == "Joes Movie 13 2011" and not any("'" in q for q in fs.queries(film)))

# ---- fake Stash
class Stash:
    def __init__(self):
        self.groups = {"G1": {"id": "G1", "scenes": [{"id": "old1", "files": [{"id": "f-old", "path": "/data/Old/Example Film 2.avi", "height": 416, "width": 720, "duration": 5100}]}]},
                       "G2": {"id": "G2", "scenes": [{"id": "a", "files": [{"id": "fa", "path": "/p/a", "height": 400, "width": 700, "duration": 1}]},
                                                     {"id": "b", "files": [{"id": "fb", "path": "/p/b", "height": 400, "width": 700, "duration": 1}]}]}}
        self.scenes = {"new1": {"id": "new1", "duration": 5000}}
        self.calls = []
    def _graphql(self, q, v):
        if "findGroup(id" in q: return {"findGroup": self.groups.get(str(v["id"]))}
        if "findScene(id" in q and "height" in q: return {"findScene": {"files": [{"path": "/data/Movies/Example Film 2 (2000)/Example Film 2 (2000) 1080p.mkv", "height": 1080}]}}
        if "findScene(id" in q: return {"findScene": {"id": v["id"], "files": [{"path": p, "duration": 5000} for p in ["/data/Movies/Example Film 2 (2000)/Example Film 2 (2000) 1080p.mkv"]]}}
        raise AssertionError(q)
    def find_scene_by_path(self, p): return {"id": "new1"} if "1080p" in p else None
    def merge_scene_into(self, src, dst): self.calls.append(("merge", src, dst))
    def scene_files(self, sid): return [{"id": "f-old", "path": "/data/Old/Example Film 2.avi"}, {"id": "f-new", "path": "/data/Movies/Example Film 2 (2000)/Example Film 2 (2000) 1080p.mkv"}]
    def set_primary_file(self, sid, fid): self.calls.append(("primary", sid, fid))
    def clean_paths(self, paths): self.calls.append(("clean", list(paths)))
stash = Stash()
recycled = []
tools = (lambda p: recycled.append(p), lambda p: p)

check("upgrade_target: one scene with one file", film_import.upgrade_target(stash, "G1")["path"] == "/data/Old/Example Film 2.avi")
check("upgrade_target: several scenes -> a reason text", isinstance(film_import.upgrade_target(stash, "G2"), str))
check("upgrade_target: a dead group -> None", film_import.upgrade_target(stash, "nope") is None)

db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
def owned(title, group, **kw):  # noqa: E302
    fid, _ = film_repo.add(db, title=title, year=2000, tpdb_id="t-" + title, duration_min=83, studio_name="Some Studio", status="wanted")
    film_repo.update(db, fid, status="imported", imported_group_id=group, upgrade_wanted=1, owned_height=416, **kw)
    return fid
fid = owned("Example Film 2", "G1")
multi = owned("Other Movie", "G2")
dead = owned("Dead Group", "gone")
check("due_upgrades lists the owned movies below the profile", {f["id"] for f in fs.due_upgrades(db, 10, 7)} == {fid, multi, dead})

# ---- 2. search
class Source:
    def __init__(self, results): self.results, self.queries = results, []
    def search(self, q): self.queries.append(q); return self.results
src = Source([{"title": "Example.Film.2.2000.480p.DVDRip", "size": 1.5e9}, {"title": "Example.Film.2.2000.1080p.WEB-DL.x264", "size": 4e9}])
out = fs.search_upgrade(db, film_repo.get(db, fid), src, stash, 720)
check("the better release becomes the review candidate, the 480p one does not", out["result"] == "needs_review" and "1080p" in out["candidate"]["title"])
check("the owned height was looked up from Stash", film_repo.get(db, fid)["owned_height"] == 416)
out = fs.search_upgrade(db, film_repo.get(db, multi), src, stash, 720)
check("a group with several scenes is not searched and says why", out["result"] == "not_searched" and film_repo.get(db, multi)["last_search_result"] == "upgrade_manual")
out = fs.search_upgrade(db, film_repo.get(db, dead), src, stash, 720)
check("a dead group leaves the upgrade list", out["result"] == "not_searched" and film_repo.get(db, dead)["upgrade_wanted"] == 0)
check("a movie with a candidate waiting is not due again", fid not in {f["id"] for f in fs.due_upgrades(db, 10, 7)})
none = fs.search_upgrade(db, (film_repo.update(db, fid, review_candidate=None, last_search_at=None), film_repo.get(db, fid))[1], Source([{"title": "Example.Film.2.2000.480p.DVDRip", "size": 1.5e9}]), stash, 720)
check("nothing better -> no_match, the movie stays on the Upgrade list", none["result"] == "no_match" and film_repo.get(db, fid)["status"] == "imported" and film_repo.get(db, fid)["upgrade_wanted"] == 1)

# ---- 3. the better file is imported: merged into the owned scene, old file recycled
path = "/data/Movies/Example Film 2 (2000)/Example Film 2 (2000) 1080p.mkv"
film_repo.update(db, fid, status="grabbed", upgrading=1, pending_import_path=json.dumps([path]), grabbed_title="Example.Film.2.2000.1080p.WEB-DL.x264")
res = film_import.finalize(db, stash, film_repo.get(db, fid, with_json=True), None, None, file_tools=tools)
f = film_repo.get(db, fid)
check("finalize: imported, off the Upgrade list, nothing pending", res["status"] == "imported" and f["status"] == "imported" and f["upgrade_wanted"] == 0 and f["upgrading"] == 0 and not f["pending_import_path"])
check("the new scene was merged INTO the owned scene (keeps its metadata and its place in the Group)", ("merge", "new1", "old1") in stash.calls)
check("the new file became primary and the old file went to the recycle bin", ("primary", "old1", "f-new") in stash.calls and recycled == ["/data/Old/Example Film 2.avi"])
check("the new height is stored", f["owned_height"] == 1080 and f["last_search_result"] == "upgraded")

# ---- 4. an import that cannot replace the file keeps both
stash.calls.clear(); recycled.clear()
film_repo.update(db, dead, status="grabbed", upgrading=1, upgrade_wanted=0, pending_import_path=json.dumps([path]))
res = film_import.finalize(db, stash, film_repo.get(db, dead, with_json=True), None, None, file_tools=tools)
check("the owned Group is gone: import_failed, nothing merged or recycled", res["status"] == "import_failed" and not stash.calls and not recycled)
film_repo.update(db, multi, status="grabbed", upgrading=1, pending_import_path=json.dumps([path, path]))
res = film_import.finalize(db, stash, film_repo.get(db, multi, with_json=True), None, None, file_tools=tools)
check("an upgrade download of several files: import_failed, nothing merged or recycled", res["status"] == "import_failed" and not stash.calls and not recycled)

# ---- 5. failures lead back to the Upgrade list, never to the wishlist
check("_fail_back: an upgrading movie returns to the Upgrade list", film_import._fail_back({"upgrading": 1}) == {"status": "imported", "upgrading": 0, "upgrade_wanted": 1})
check("_fail_back: an ordinary movie returns to the wishlist", film_import._fail_back({"upgrading": 0}) == {"status": "wanted"})
fid2 = owned("Lost Download", "G1")
film_repo.update(db, fid2, status="grabbed", upgrading=1, upgrade_wanted=0, download_client_id=1, download_job_id="j1", grabbed_title="Lost.Download.1080p")
import download_client_repo  # noqa: E402
download_client_repo.get_one = lambda db_, i: {"type": "sabnzbd", "id": 1}
download_jobs.job_state = lambda row, jid: {"state": "failed", "client": "SABnzbd", "fail_message": "Duplicate NZB"}
film_import.run(db, stash, None, None, {}, file_tools=tools)
f2 = film_repo.get(db, fid2)
check("a failed upgrade download: back on the Upgrade list with the release blocklisted", f2["status"] == "imported" and f2["upgrade_wanted"] == 1 and f2["upgrading"] == 0 and "Lost.Download.1080p" in f2["rejected_titles"])
sys.exit(1 if FAILS else 0)
