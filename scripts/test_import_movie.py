"""Tests (v0.190.0): Import folder -- a scene in exactly one Stash group goes to Movies/<Title> (<Year>)/... Fake Stash, no network.
Run:  venv/bin/python scripts/test_import_movie.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import film_names  # noqa: E402
from library_import import Importer  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

b = film_names.base_name
check("title + year from the group date", b("Example Movie Title", "2013-02-19") == "Example Movie Title (2013)")
check("a year already in the name is kept once", b("Sample Film (2011)", "2011-05-01") == "Sample Film (2011)")
check("... and wins over the date", b("Sample Film (2011)", "2012-01-01") == "Sample Film (2011)")
check("sorted article is put back in front", b("Example Parody, The (2012)") == "The Example Parody (2012)")
check("no year anywhere: just the title", b("Some Film") == "Some Film" and b("Some Film", "") == "Some Film")
check("characters a folder cannot have are replaced", b('What: A "Film"?', "2001-01-01") == "What A Film (2001)")

class FakeStash:
    stashdb_endpoint = "https://stashdb.org/graphql"
    theporndb_endpoint = "https://theporndb.net/graphql"
    def __init__(self, scenes): self.scenes = scenes  # noqa: E704
    def _graphql(self, q, v): return {"findScenes": {"scenes": self.scenes}}  # noqa: E704
    def find_scene_by_stashdb_id(self, *a, **k): return None  # noqa: E704

def scene(i, path, groups, organized=False, stash_ids=(), height=1080, name="T"):
    return {"id": str(i), "title": name, "date": None, "organized": organized, "studio": None, "tags": [], "stash_ids": list(stash_ids), "groups": groups,
            "files": [{"id": f"f{i}", "path": path, "width": 1920, "height": height, "duration": 6000, "mod_time": "2020-01-01", "fingerprints": []}]}
def grp(name, date, urls, count=1, idx=None):  # noqa: E302
    return {"scene_index": idx, "group": {"id": "1", "name": name, "date": date, "scene_count": count, "urls": urls}}
TPDB = ["https://theporndb.net/movies/some-film"]

root = tempfile.mkdtemp()
imp = object.__new__(Importer)
imp._root = lambda: "/data"
imp.to_local = lambda p: p.replace("/data", root, 1)
imp.roots = lambda: ["/data/Scenes"]
imp._is_vr = lambda s, f, r: False
imp._identify = lambda scenes: {}
imp._verify = lambda pairs: {}
SETTINGS = {"movie_folder_format": film_names.DEFAULT_FOLDER_FORMAT, "movie_naming_format": film_names.DEFAULT_FILE_FORMAT,
            "import_sources": '["/data/Scenes", "/data/import"]'}
imp.setting = lambda key: SETTINGS.get(key, "")

def run(*scenes):
    imp.stash = FakeStash(list(scenes))
    r = imp.analyse("/data/Scenes")
    return {i["scene_id"]: i for i in r["items"]}, r

items, r = run(scene(1, "/data/Scenes/__Moving/EXAMPLE.mp4.flv", [grp("Example Movie Title", "2013-02-19", TPDB)]))
i = items["1"]
check("the real case: ThePornDB movie link -> kind movie", i["kind"] == "movie")
check("target = Movies/<Title> (<Year>)/<Title> (<Year>) <Quality>.<ext>", i["new_path"] == "/data/Movies/Example Movie Title (2013)/Example Movie Title (2013) WEBDL-1080p.flv")
check("counted as movie, listed", r["counts"].get("movie") == 1)

items, _ = run(scene(2, "/data/Scenes/a/x.mkv", [grp("Film", "2010-01-01", [])], organized=True))
check("no link but marked Organized -> movie", items["2"]["kind"] == "movie" and "Organized" in items["2"]["how"])
items, _ = run(scene(3, "/data/Scenes/a/x.mkv", [grp("Film", "2010-01-01", [])], organized=False))
check("neither link nor Organized -> stays unknown, says why", items["3"]["kind"] == "unknown" and "Organized" in items["3"]["how"] and not items["3"]["new_path"])

items, _ = run(scene(4, "/data/Scenes/a/p2.mkv", [grp("Film", "2010-01-01", TPDB, count=3, idx=2)]))
check("a group with several scenes: ' - part N'", items["4"]["new_path"] == "/data/Movies/Film (2010)/Film (2010) - part 2 WEBDL-1080p.mkv")
items, _ = run(scene(5, "/data/Scenes/a/p.mkv", [grp("Film", "2010-01-01", TPDB, count=3, idx=None)]))
check("several scenes but no scene number -> review", items["5"]["kind"] == "review")

items, _ = run(scene(6, "/data/Scenes/a/x.mkv", [grp("Film", "2010-01-01", TPDB), grp("Other", "2011-01-01", TPDB)]))
check("a scene in TWO groups is not treated as a movie", items["6"]["kind"] != "movie")
items, _ = run(scene(7, "/data/Scenes/a/x.mkv", [grp("Film", "2010-01-01", TPDB)], stash_ids=[{"endpoint": "https://stashdb.org/graphql", "stash_id": "abc"}]))
check("a scene WITH a provider id keeps the scene route (not a movie)", items["7"]["kind"] != "movie")

os.makedirs(f"{root}/Movies/Film (2010)")
open(f"{root}/Movies/Film (2010)/Film (2010) WEBDL-1080p.mkv", "w").write("x")
items, _ = run(scene(8, "/data/Scenes/a/x.mkv", [grp("Film", "2010-01-01", TPDB)]))
check("target name already taken -> review, nothing overwritten", items["8"]["kind"] == "review" and "already exists" in items["8"]["how"])

items, _ = run(scene(9, "/data/Scenes/a/f1.mkv", [grp("Same", "2010-01-01", TPDB)]), scene(10, "/data/Scenes/b/f2.mkv", [grp("Same", "2010-01-01", TPDB)]))
check("two files for the same target -> both review", items["9"]["kind"] == "review" and items["10"]["kind"] == "review")

items, _ = run(scene(11, "/data/Scenes/a/x.mkv", [grp("Film", "2010-01-01", TPDB)], height=380))
check("quality token from the file's height", items["11"]["new_path"].endswith(" WEBDL-480p.mkv"))

# ---- the "Organized" hook: Importer.organized_scene() ----
import library_import  # noqa: E402
NOTES = []
library_import.history.add = lambda *a, **k: NOTES.append((a, k))
APPLIED = []
imp._apply_items = lambda label, items: (APPLIED.append((label, items)), {"done": items, "failed": [], "leftovers": []})[1]
imp.queue_guard = lambda force=False: None
def hook(sc, mode):  # noqa: E302
    NOTES.clear(); APPLIED.clear()
    imp._scene_by_id = lambda sid: sc
    imp.stash = FakeStash([sc] if sc else [])
    return imp.organized_scene(sc["id"] if sc else 0, mode)

movie = scene(20, "/data/Scenes/__Moving/m.flv", [grp("Film", "2010-01-01", TPDB)], organized=True)
check("hook off: nothing", hook(movie, "off") == {"skipped": "off"} and not APPLIED)
check("hook on a scene that is not (any more) Organized: nothing", hook(scene(21, "/data/Scenes/a/x.mkv", [grp("Film", "2010-01-01", TPDB)], organized=False), "on") == {"skipped": "not organized"})
r = hook(movie, "preview")
check("preview: the plan is returned and noted in History, nothing moved", r["preview"]["kind"] == "movie" and not APPLIED and any("preview" in str(n[0]).lower() for n in NOTES))
r = hook(movie, "on")
check("on: the movie scene is applied", len(APPLIED) == 1 and APPLIED[0][1][0]["new_path"] == "/data/Movies/Film (2010)/Film (2010) WEBDL-1080p.flv")
r = hook(scene(22, "/data/Scenes/a/x.mkv", [], organized=True), "on")
check("organized, but no group and no provider id: left alone, History says why", "note" in r and not APPLIED and NOTES)
r = hook(scene(23, "/somewhere/else/x.mkv", [grp("Film", "2010-01-01", TPDB)], organized=True), "on")
check("a file outside the library is never moved", "note" in r and not APPLIED)
r = hook(scene(24, "/data/scenes/_UNMATCHED/x.mkv", [grp("Film", "2010-01-01", TPDB)], organized=True), "on")
check("a file parked in _UNMATCHED is never moved", "note" in r and not APPLIED)
two = scene(25, "/data/Scenes/a/x.mkv", [grp("Film", "2010-01-01", TPDB)], organized=True); two["files"].append(dict(two["files"][0], id="f25b"))
check("a scene with two files is not moved", "note" in hook(two, "on") and not APPLIED)
inplace = scene(26, "/data/Movies/Film (2010)/Film (2010) WEBDL-1080p.mkv", [grp("Film", "2010-01-01", TPDB)], organized=True)
check("already where it belongs: nothing to do", "note" in hook(inplace, "on") and not APPLIED)
imp.queue_guard = lambda force=False: {"error": "Stash's job queue is busy"}
r = hook(movie, "on")
check("Stash busy (Generate task ...): not moved, History says so", "error" in r and not APPLIED and any("busy" in str(n).lower() for n in NOTES))
print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
