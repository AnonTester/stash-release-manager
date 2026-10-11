"""Tests of stage D (v0.160.0): film_whisparr.find_duplicate_entry / run() adoption and film_library.check / apply_one against a THROWAWAY database
with a FAKE Whisparr and a FAKE Stash. Run:  venv/bin/python scripts/test_film_library.py  -- never touches the live database or any service."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import film_import  # noqa: E402
import film_library  # noqa: E402
import film_repo  # noqa: E402
import film_whisparr  # noqa: E402
from db import Database  # noqa: E402


class FakeResp:
    def __init__(self, data): self._d = data
    def raise_for_status(self): pass
    def json(self): return self._d


class FakeSession:
    def __init__(self, lookup): self.lookup = lookup
    def get(self, url, params=None, timeout=None): return FakeResp(self.lookup.get(params["term"], []))


class FakeWhisparr:
    def __init__(self, movies, lookup):
        self.movies, self.base, self.timeout, self.session = movies, "http://w/api/v3", 5, FakeSession(lookup)
        self.set_monitored, self.registered = [], []
    def movies_by_title_year(self, title, year):
        n = lambda t: "".join(c for c in (t or "").lower() if c.isalnum())  # noqa: E731
        return [m for m in self.movies if n(m["title"]) == n(title) and (not year or m.get("year") == year)]
    def movie_cached(self, mid): return next((m for m in self.movies if m["id"] == mid), None)
    def set_movie_monitored(self, mid, value): self.set_monitored.append((mid, value))
    def register_existing(self, item, profile, root): self.registered.append(item["title"]); return {"id": 9000 + len(self.registered)}
    request_download = register_existing


class FakeStash:
    def __init__(self, scenes): self.scenes = scenes  # path -> {"id", "files", "groups"}
    def find_scene_by_path(self, path): s = self.scenes.get(path); return {"id": s["id"]} if s else None
    def _graphql(self, query, variables):
        s = next(v for v in self.scenes.values() if v["id"] == variables["id"])
        return {"findScene": {"id": s["id"], "files": s["files"], "groups": [{"group": g} for g in s["groups"]]}}


FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)


def add(db, title, year, minutes, tpdb, status="wanted", **kw):
    fid, _ = film_repo.add(db, title=title, year=year, tpdb_id=tpdb, duration_min=minutes)
    film_repo.update(db, fid, status=status, **kw)
    return fid


def entry(mid, title, year, foreign, runtime=None, path=None, minutes=None):
    e = {"id": mid, "title": title, "year": year, "foreignId": foreign, "monitored": True, "runtime": runtime}
    if path:
        e["path"] = os.path.dirname(path)
        e["movieFile"] = {"path": path, "mediaInfo": {"runTime": f"{int(minutes // 60)}:{int(minutes % 60):02d}:00"}}
    return e


# ---- D1: adoption / conflict at the Whisparr sync
db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
craving = add(db, "The Sample Alpha", 2007, 80.6, "ours-1")
hangover = add(db, "The Example Parody", 2012, 70.1, "ours-2")
twin = add(db, "Twin Sample XXX", 2010, 90.0, "ours-3")
plain = add(db, "Plain Movie", 2015, 60.0, "ours-4")
heart = add(db, "Alpha Strings", 2011, 88.0, "ours-5", status="imported", imported_group_id="1210")
movies = [entry(10, "The Sample Alpha", 2007, "71519", path="/data/Movies/The Sample Alpha (2007)/The Sample Alpha (2007) WEBDL-480p.avi", minutes=81),
          entry(11, "The Example Parody", 2012, "456979", path="/data/Movies/H/H.mp4", minutes=153),
          entry(12, "Twin Sample XXX", 2010, "a"), entry(13, "Twin Sample XXX", 2010, "b"),
          entry(14, "Alpha Strings", 2011, "tpdbId:8b3a5be4")]
lookup = {f"{t} {y}": [{"foreignId": f"tpdbId:ours-{i}", "title": t, "year": y}] for i, (t, y) in enumerate(
    [("", 0), ("The Sample Alpha", 2007), ("The Example Parody", 2012), ("Twin Sample XXX", 2010), ("Plain Movie", 2015), ("Alpha Strings", 2011)])}
fw = FakeWhisparr(movies, lookup)
r = film_whisparr.run(db, fw, "bookkeeping_only", 1, "/data/", dry_run=True, pause=0, all_movies=True)
check("dry run: Craving and Alpha Strings would be adopted", sorted(x.split(" -- ")[0] for x in r["adopt"]) == ["Alpha Strings", "The Sample Alpha"])
check("dry run: Hangover (153 vs 70 min) and the twin (2 entries) are conflicts", sorted(x.split(" -- ")[0] for x in r["conflict"]) == ["The Example Parody", "Twin Sample XXX"])
check("dry run: the plain one would be registered, nothing written", r["register"] == ["Plain Movie"] and not fw.registered and film_repo.get(db, craving)["whisparr_id"] is None)
r = film_whisparr.run(db, fw, "bookkeeping_only", 1, "/data/", dry_run=False, pause=0, all_movies=True)
f = film_repo.get(db, craving)
check("apply: Craving uses Whisparr's entry 10 (state adopted + note), set to unmonitored (bookkeeping)", f["whisparr_id"] == 10 and f["whisparr_state"] == "adopted" and "same movie" in f["whisparr_note"] and (10, False) in fw.set_monitored)
f = film_repo.get(db, hangover)
check("apply: Hangover stays without an entry, state conflict with the reason", f["whisparr_id"] is None and f["whisparr_state"] == "conflict" and "153" in f["whisparr_note"])
check("apply: Alpha Strings (imported) adopts entry 14", film_repo.get(db, heart)["whisparr_id"] == 14)
check("apply: Plain Movie registered normally", film_repo.get(db, plain)["whisparr_state"] == "registered")

# ---- D2: already in the library
db2 = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
remy = add(db2, "Remy 2", 2013, 163.0, "r")      # grouped scene, length fits -> clear link_group
crav = add(db2, "The Sample Alpha", 2007, 80.6, "c")   # scene without group, length fits -> clear make_group
hang = add(db2, "The Example Parody", 2012, 70.1, "h")  # length does not fit -> manual
nofile = add(db2, "No File Movie", 2012, 70.0, "n")
kept = add(db2, "Kept Movie", 2012, 70.0, "k"); film_repo.update(db2, kept, library_choice="keep")
P = lambda n: f"/data/Movies/{n}/{n}.mp4"  # noqa: E731
mv = [entry(1, "Remy 2", 2013, "old1", path=P("Remy 2"), minutes=164), entry(2, "The Sample Alpha", 2007, "old2", path=P("The Sample Alpha"), minutes=81),
      entry(3, "The Example Parody", 2012, "old3", path=P("Hang"), minutes=153), entry(4, "No File Movie", 2012, "old4"),
      entry(5, "Kept Movie", 2012, "old5", path=P("Kept"), minutes=70)]
fw2 = FakeWhisparr(mv, {})
st = FakeStash({P("Remy 2"): {"id": "s1", "files": [{"path": P("Remy 2"), "width": 720, "height": 400, "duration": 164 * 60, "size": 1.6e9}], "groups": [{"id": "846", "name": "Remy 2"}]},
                P("The Sample Alpha"): {"id": "s2", "files": [{"path": P("The Sample Alpha"), "width": 512, "height": 288, "duration": 81 * 60, "size": 7e8}], "groups": []},
                P("Hang"): {"id": "s3", "files": [{"path": P("Hang"), "width": 1920, "height": 1080, "duration": 153 * 60, "size": 9e9}], "groups": []},
                P("Kept"): {"id": "s5", "files": [{"path": P("Kept"), "width": 1, "height": 1, "duration": 4200, "size": 1}], "groups": []}})
res = film_library.check(db2, fw2, st)
check("check: Remy 2 + The Sample Alpha are clear", sorted(v["title"] for v in res["clear"]) == ["Remy 2", "The Sample Alpha"])
check("check: Hangover is manual (length does not fit)", [v["title"] for v in res["manual"]] == ["The Example Parody"])
check("check: no-file movie counts as 'other', the kept one is not listed at all", res["other"] == 1 and all(v["title"] != "Kept Movie" for v in res["clear"] + res["manual"]))
verdicts = {v["title"]: v for v in res["clear"] + res["manual"]}
check("check: the actions fit the groups", verdicts["Remy 2"]["action"] == "link_group" and verdicts["The Sample Alpha"]["action"] == "make_group")
out = film_library.apply_one(db2, st, None, None, film_repo.get(db2, remy), verdicts["Remy 2"])
f = film_repo.get(db2, remy)
check("apply link_group: imported with the existing group, nothing downloaded", out["status"] == "imported" and f["status"] == "imported" and f["imported_group_id"] == "846")
calls = []
film_import_finalize = film_import.finalize
film_import.finalize = lambda db, stash, film, movies, gp, force=False: (calls.append((film["pending_import_path"], force)) or {"status": "imported"})
out = film_library.apply_one(db2, st, None, None, film_repo.get(db2, crav), verdicts["The Sample Alpha"])
film_import.finalize = film_import_finalize
check("apply make_group: the existing file goes through the normal finalize (path recorded)", out["status"] == "imported" and calls and "The Sample Alpha.mp4" in calls[0][0] and calls[0][1] is False)
out = film_library.apply_one(db2, st, None, None, film_repo.get(db2, remy), verdicts["Remy 2"])
check("apply again: a movie that is not wanted any more is refused", "error" in out)
film_library.keep(db2, hang)
check("keep: the movie leaves the list", all(v["title"] != "The Example Parody" for v in film_library.check(db2, fw2, st)["manual"]))

print("\n%s" % ("ALL OK" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
