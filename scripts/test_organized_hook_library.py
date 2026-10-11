"""Tests of the Organized hook for a scene with a provider id in the Whisparr library folder scenes/ (v0.203.0): preview / on / guards, against throwaway
files and fakes. Run:  venv/bin/python scripts/test_organized_hook_library.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import library_import  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)


mount = tempfile.mkdtemp()
def local(p): return mount + p[len("/data"):]  # noqa: E302
OLD = "/data/scenes/Wrong Folder/old name.mp4"
os.makedirs(os.path.dirname(local(OLD)))
open(local(OLD), "wb").write(b"x")
open(local("/data/scenes/Wrong Folder/old name.nfo"), "wb").write(b"n")

SCENE = {"id": "7", "title": "Old Title", "date": "2020-01-02", "organized": True,
         "studio": {"id": "5", "name": "studio", "stash_ids": [{"endpoint": "https://stashdb.org/graphql", "stash_id": "stu-1"}]},
         "tags": [], "stash_ids": [{"endpoint": "https://stashdb.org/graphql", "stash_id": "sid-1"}],
         "groups": [], "files": [{"id": "f7", "path": OLD, "width": 1920, "height": 1080, "duration": 100, "mod_time": "2020-02-01T00:00:00Z",
                                  "fingerprints": [{"type": "phash", "value": "ff"}, {"type": "oshash", "value": "ab"}]}]}


class FakeStash:
    stashdb_endpoint = "https://stashdb.org/graphql"
    theporndb_endpoint = "https://theporndb.net/graphql"
    new_path = None
    def _graphql(self, q, v=None):  # noqa: E301
        if "jobQueue" in q: return {"jobQueue": []}
        if "plugins" in q: return {"plugins": []}
        return {"findScenes": {"scenes": [SCENE]}}
    def scan_paths(self, paths): return "job"  # noqa: E704
    def wait_for_job(self, *a, **k): return None  # noqa: E704
    def find_scene_by_path(self, path): return {"id": "7"} if path in (SCENE["files"][0]["path"], self.new_path) else None  # noqa: E704


class FakeStashDB:
    def find_scenes_by_fingerprints(self, sets): return [[{"id": "sid-1", "title": "Real Title"}] for _ in sets]  # noqa: E704
    def get_scene_detail(self, sid): return {"title": "Real Title", "date": "2021-03-04"}  # noqa: E704
    def get_studio_name(self, sid): return "Real Studio"  # noqa: E704


SETTINGS = {"organized_hook": "on", "whisparr_root_folder_path": "/data", "import_sources": "/data/Scenes",
            "scene_folder_format": "scenes/{Studio Title}", "scene_naming_format": "{Scene Title} {Quality Full}",
            "movie_folder_format": "Movies/{Movie Title}", "movie_naming_format": "{Movie Title} {Quality Full}"}
db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))
stash = FakeStash()
imp = library_import.Importer(db, stash, FakeStashDB(), None, local, lambda k: SETTINGS.get(k, ""))
imp.DRIFT_WATCH_SECONDS = 0

r = imp.organized_scene("7", "preview")
expected = "/data/scenes/Real Studio/Real Title WEBDL-1080p.mp4"
check("preview: planned path from the provider (studio spelling, title), nothing moved",
      r.get("preview", {}).get("new_path") == expected and os.path.exists(local(OLD)))

SCENE["organized"] = False
check("not organized: skipped", imp.organized_scene("7", "on").get("skipped") == "not organized")
SCENE["organized"] = True

stash.new_path = expected
r = imp.organized_scene("7", "on")
check("on: moved and confirmed", len(r.get("done", [])) == 1 and not r.get("failed"))
check("file and same-name extra at the new place, old folder gone",
      os.path.exists(local(expected)) and os.path.exists(local(expected[:-4] + ".nfo")) and not os.path.exists(local("/data/scenes/Wrong Folder")))

# nothing to do when it already sits there
SCENE["files"][0]["path"] = expected
r = imp.organized_scene("7", "on")
check("already in place: a note, no move", "already" in (r.get("note") or ""))

# a scene WITHOUT a provider id is not this hook's business
SCENE["stash_ids"] = []
r = imp.organized_scene("7", "on")
check("no provider id: not moved, a note", "note" in r and not r.get("done"))
print("FAILED: %s" % FAILS if FAILS else "all ok")
sys.exit(1 if FAILS else 0)
