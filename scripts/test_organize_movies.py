"""Tests of Organize library for existing films (v0.202.0): movies_plan / movies_apply against throwaway files and a fake Stash.
Run:  venv/bin/python scripts/test_organize_movies.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import library_organize  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)


mount = tempfile.mkdtemp()  # plays the container's /media; Stash's /data maps to it
def local(p): return mount + p[len("/data"):]  # noqa: E302
def touch(path):  # noqa: E302
    os.makedirs(os.path.dirname(local(path)), exist_ok=True)
    open(local(path), "wb").write(b"x")

SETTINGS = {"whisparr_root_folder_path": "/data", "movie_folder_format": "Movies/{Movie Title}< ({Movie Year})>",
            "movie_naming_format": "{Movie Title}< ({Movie Year})>< - part {Part Number}> {Quality Full}"}
G1 = {"id": "1", "name": "Big Film", "date": "2013-05-01", "scene_count": 1, "studio": None}
G2 = {"id": "2", "name": "Two Parts (2010)", "date": None, "scene_count": 2, "studio": None}
FILES = {
    "ok": "/data/Movies/Big Film (2013)/Big Film (2013) WEBDL-1080p.mp4",
    "rename": "/data/Movies/Big Film (2013)/bigfilm.mp4",
}
SCENES = [
    {"id": "10", "title": "a", "files": [{"id": "f10", "path": "/data/Movies/Big Film (2013)/Big Film (2013) WEBDL-1080p.mp4", "height": 1080}], "groups": [{"scene_index": None, "group": G1}]},
    {"id": "11", "title": "b", "files": [{"id": "f11", "path": "/data/Movies/Two Parts (2010)/cd1.mp4", "height": 720}], "groups": [{"scene_index": 1, "group": G2}]},
    {"id": "12", "title": "c", "files": [{"id": "f12", "path": "/data/Movies/Two Parts (2010)/cd2.mp4", "height": 720}], "groups": [{"scene_index": None, "group": G2}]},
    {"id": "13", "title": "d", "files": [{"id": "f13", "path": "/data/Movies/Anal/some.mp4", "height": 1080}], "groups": [{"scene_index": None, "group": {**G1, "id": "3", "name": "Other Film (2001)", "date": "2001-01-01"}}]},
    {"id": "15", "title": "f", "files": [{"id": "f15", "path": "/data/Movies/Edition Film (DVD + Blu-ray Combo) (2010)/Edition Film (DVD + Blu-ray Combo) (2010) WEBDL-480p.mp4", "height": 480}], "groups": [{"scene_index": None, "group": {**G1, "id": "4", "name": "Edition Film (DVD + Blu-ray Combo)", "date": "2010-03-03"}}]},
    {"id": "14", "title": "e", "files": [{"id": "f14", "path": "/data/scenes/Studio/x.mp4", "height": 1080}], "groups": [{"scene_index": None, "group": G1}]},
]
for sc in SCENES:
    for f in sc["files"]:
        touch(f["path"])
touch("/data/Movies/Anal/some.nfo")  # not a same-stem extra of some.mp4 ("some.nfo" is: same stem) -> follows
touch("/data/Movies/Anal/folder.jpg")


class FakeStash:
    stashdb_endpoint = "https://stashdb.org/graphql"
    theporndb_endpoint = "https://theporndb.net/graphql"
    new_paths = {}
    def _graphql(self, q, v=None):
        if "jobQueue" in q: return {"jobQueue": []}
        return {"findScenes": {"scenes": SCENES}}
    def scan_paths(self, paths): return "job"
    def wait_for_job(self, *a, **k): return None
    def find_scene_by_path(self, path):  # a real Stash would have rescanned the moved file: scene keeps its id
        for sc in SCENES:
            if sc["files"][0]["path"] == path or self.new_paths.get(sc["id"]) == path: return {"id": sc["id"]}
        return None


db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))
stash = FakeStash()
org = library_organize.Organizer(db, stash, None, None, local, lambda k: SETTINGS.get(k))
org.DRIFT_WATCH_SECONDS = 0

plan = org.movies_plan()
check("in-place film counted", plan["in_place"] == 1)
check("only movie-root files are looked at", all("scenes/" not in x["path"] for k in ("rename", "move") for x in plan[k]))
check("two-part film: scene without number needs a check", [x["file_id"] for x in plan["needs_check"]] == ["f12"])
renames = {x["file_id"]: x["new_path"] for x in plan["rename"]}
check("part 1 renamed in its folder", renames.get("f11") == "/data/Movies/Two Parts (2010)/Two Parts (2010) - part 1 WEBDL-720p.mp4")
moves = {x["file_id"]: x["new_path"] for x in plan["move"]}
check("film in a genre folder moves to its own folder", moves.get("f13") == "/data/Movies/Other Film (2001)/Other Film (2001) WEBDL-1080p.mp4")

stash.new_paths = {x["scene_id"]: x["new_path"] for k in ("rename", "move") for x in plan[k]}
ed = [x for x in plan["move"] if x["file_id"] == "f15"]
check("edition note in the folder name: offered as its own kind, target without it",
      len(ed) == 1 and ed[0]["diff"] == "edition note in the name" and ed[0]["new_path"] == "/data/Movies/Edition Film (2010)/Edition Film (2010) WEBDL-480p.mp4")
res = org.movies_apply(["f11", "f13"], ignore_renamers=True)
check("apply done", len(res.get("done", [])) == 2 and not res.get("failed"))
check("files on disk moved", os.path.exists(local("/data/Movies/Other Film (2001)/Other Film (2001) WEBDL-1080p.mp4"))
      and not os.path.exists(local("/data/Movies/Anal/some.mp4")))
check("same-stem extra renamed along", os.path.exists(local("/data/Movies/Other Film (2001)/Other Film (2001) WEBDL-1080p.nfo")))
check("folder.jpg follows the only video, genre folder removed", os.path.exists(local("/data/Movies/Other Film (2001)/folder.jpg"))
      and not os.path.exists(local("/data/Movies/Anal")))
check("movie roots exist (the root itself stays)", os.path.isdir(local("/data/Movies")))
print("FAILED: %s" % FAILS if FAILS else "all ok")
sys.exit(1 if FAILS else 0)
