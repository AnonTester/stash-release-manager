"""Tests (v0.226.0): Import folder -- sources are added/removed in the UI (setting import_sources), a new folder becomes a Stash library
path + scan, the duplicate review carries a quality hint. Fake Stash, temp folders, no network.
Run:  venv/bin/python scripts/test_import_sources.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import history  # noqa: E402
from library_import import Importer  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

history.add = lambda *a, **k: None

class FakeDb:
    def __init__(self): self.rows = {}  # noqa: E704
    def connect(self): return self  # noqa: E704
    def __enter__(self): return self  # noqa: E704
    def __exit__(self, *a): return False  # noqa: E704
    def execute(self, sql, params=()): self.rows["import_sources"] = params[0]  # noqa: E704

class FakeStash:
    stashdb_endpoint = "sdb"; theporndb_endpoint = "tpdb"  # noqa: E702
    def __init__(self, libs): self.libs = libs; self.scanned = []; self.held = {}  # noqa: E702
    def library_config(self): return [dict(x) for x in self.libs]  # noqa: E704
    def set_library_config(self, l): self.libs = [dict(x) for x in l]; return self.library_config()  # noqa: E702
    def scan_paths(self, paths): self.scanned.append(paths); return "7"  # noqa: E702
    def _graphql(self, q, v): return {"findScene": {"files": self.held[v["id"]]}}  # noqa: E704

root = tempfile.mkdtemp()
for d, n in (("data/scenes", "a.mp4"), ("data/Movies", "m.mp4"), ("data/new1", "x.mp4"), ("data/new1/sub", "y.mkv"), ("data/empty", "r.txt"), ("data/import", "i.mp4")):
    os.makedirs(os.path.join(root, d), exist_ok=True)
    open(os.path.join(root, d, n), "w").close()

stash = FakeStash([{"path": "/data/scenes", "excludeVideo": False, "excludeImage": False}, {"path": "/data/Movies", "excludeVideo": False, "excludeImage": False}])
db = FakeDb()
imp = object.__new__(Importer)
imp.db, imp.stash = db, stash
imp.to_local = lambda p: p.replace("/data", root + "/data", 1)
imp._root = lambda: "/data"
imp.movie_roots = lambda: ["/data/Movies"]
imp.setting = lambda key: db.rows.get(key, "")
backups = tempfile.mkdtemp()

# nothing saved yet: only the old default folders that exist
check("unsaved: only existing defaults", imp.roots() == ["/data/import"])

r = imp.add_check("/data/new1")
check("a plain folder can be added, Stash gets a library path", r["can_add"] and r["will_add_library"] and r["videos"] == 2)
check("the library folder is refused", not imp.add_check("/data/scenes")["can_add"])
check("a folder inside the library is refused", not imp.add_check("/data/scenes/x")["can_add"])
check("a folder holding the library is refused", not imp.add_check("/data")["can_add"])
check("the movie folder is refused", not imp.add_check("/data/Movies")["can_add"])
check("a missing folder is refused", not imp.add_check("/data/nope")["can_add"])
check("a folder without videos is refused", not imp.add_check("/data/empty")["can_add"])
check("relative paths are refused", not imp.add_check("new1")["can_add"])
check("the dry run changed nothing", len(stash.libs) == 2 and not stash.scanned and "import_sources" not in db.rows)

res = imp.add_source("/data/new1", backups)
check("added: library path + scan + saved list", res["done"] and any(x["path"] == "/data/new1" for x in stash.libs)
      and stash.scanned == [["/data/new1"]] and imp.roots() == ["/data/import", "/data/new1"])
check("the old library list was saved", res["backup"] and os.path.isfile(res["backup"]))
check("images are not scanned in the new library", [x for x in stash.libs if x["path"] == "/data/new1"][0]["excludeImage"] is True)
check("adding twice is refused", not imp.add_source("/data/new1", backups)["done"])

# a folder below an existing library path: no new library entry, only the scan
stash.libs.append({"path": "/data/import", "excludeVideo": False, "excludeImage": False})
os.makedirs(os.path.join(root, "data/import/deep"), exist_ok=True)
open(os.path.join(root, "data/import/deep/z.mp4"), "w").close()
r = imp.add_check("/data/import/deep")
check("covered by a library: no new entry", r["can_add"] and not r["will_add_library"] and r["covered_by"] == "/data/import")
before = len(stash.libs)
imp.add_source("/data/import/deep", backups)
check("... only the scan runs", len(stash.libs) == before and stash.scanned[-1] == ["/data/import/deep"])

# a folder that holds an existing library path
stash.libs.append({"path": "/data/new1/sub", "excludeVideo": False, "excludeImage": False})
check("a folder with library paths below it is refused", not imp.add_check("/data/new1")["can_add"] or "/data/new1" in imp.roots())

# remove
check("remove drops it from the list only", imp.remove_source("/data/new1")["removed"] and "/data/new1" not in imp.roots()
      and any(x["path"] == "/data/new1" for x in stash.libs))
check("removing an unknown folder says so", not imp.remove_source("/data/zzz")["removed"])
db.rows["import_sources"] = "[]"
check("a saved empty list stays empty (no defaults)", imp.roots() == [])

# quality hint
stash.held = {"1": [{"width": 1920, "height": 1080, "bit_rate": 8_000_000}], "2": [{"width": 1280, "height": 720, "bit_rate": 3_000_000}]}
note = imp._held_note({"scene_id": "1"}, "2")
check("hint: better", "looks better" in note and "1920x1080" in note and "3.0 Mbit/s" in note)
note = imp._held_note({"scene_id": "2"}, "1")
check("hint: not better", "is not better" in note)
stash.held["2"] = [{"width": 1920, "height": 1080, "bit_rate": 8_000_000}]
check("hint: equal", "equal quality" in imp._held_note({"scene_id": "1"}, "2"))
stash.held["2"] = [{"width": None, "height": None, "bit_rate": None}]
check("hint: nothing without a size", imp._held_note({"scene_id": "1"}, "2") == "")

# a saved analysis is told stale by the signature
sc = lambda groups=(), org=False: [{"id": "1", "files": [{"path": "/x/a.mp4"}], "organized": org, "stash_ids": [], "groups": list(groups)}]  # noqa: E731
grp = lambda urls: {"scene_index": None, "group": {"id": "5", "urls": urls}}  # noqa: E731
check("signature: same state, same value", Importer.signature(sc()) == Importer.signature(sc()))
check("signature: a Group changes it", Importer.signature(sc()) != Importer.signature(sc([grp([])])))
check("signature: a ThePornDB link changes it", Importer.signature(sc([grp([])])) != Importer.signature(sc([grp(["https://theporndb.net/movies/x"])])))
check("signature: Organized changes it", Importer.signature(sc()) != Importer.signature(sc(org=True)))

print("\nFAILED: " + ", ".join(FAILS) if FAILS else "\nall ok")
sys.exit(1 if FAILS else 0)
