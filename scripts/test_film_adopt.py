"""A wishlist movie whose Stash Group has a file now (v0.221.12): film_import.adopt_filled_groups against a THROWAWAY database and a fake Stash.
Run:  venv/bin/python scripts/test_film_adopt.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import film_import  # noqa: E402
import film_repo  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

def f(minutes, w, h): return {"duration": minutes * 60, "width": w, "height": h}  # noqa: E302
GROUPS = [
    {"id": "1", "scenes": [{"files": [f(85, 640, 480)]}]},                       # covers 83 min, 480p  -> upgrade
    {"id": "2", "scenes": [{"files": [f(120, 1920, 1080)]}]},                    # covers 120 min, 1080p -> done
    {"id": "3", "scenes": [{"files": [f(20, 1920, 1080)]}]},                     # only 20 of 120 min -> stays wanted
    {"id": "4", "scenes": []},                                                   # empty -> stays wanted
    {"id": "5", "scenes": [{"files": [f(60, 1280, 536)]}, {"files": [f(58, 1280, 536)]}]},  # two parts, letterboxed 720p -> done
    {"id": "6", "scenes": [{"files": [f(83, 640, 480)]}]},                       # no length known -> left alone
    {"id": "7", "scenes": [{"files": [f(83, 640, 480)]}]},                       # parts were added by hand -> left alone
]
class Stash:
    def _graphql(self, q, v): return {"findGroups": {"groups": GROUPS}}
class EmptyStash:
    def _graphql(self, q, v): return {"findGroups": {"groups": []}}

db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
ids = {}
for gid, minutes in (("1", 83), ("2", 120), ("3", 120), ("4", 120), ("5", 118), ("6", None), ("7", 83)):
    fid, _ = film_repo.add(db, title=f"Movie {gid}", year=2000, tpdb_id=f"t{gid}", duration_min=minutes, status="wanted")
    film_repo.update(db, fid, stash_group_id=gid, source="empty-group")
    ids[gid] = fid
film_repo.update(db, ids["7"], parts_imported=2)

dry = film_import.adopt_filled_groups(db, Stash(), 720, dry_run=True)
check("dry run lists the three covered movies and changes nothing", {d["title"] for d in dry} == {"Movie 1", "Movie 2", "Movie 5"}
      and all(film_repo.get(db, i)["status"] == "wanted" for i in ids.values()))
out = film_import.adopt_filled_groups(db, Stash(), 720)
by = {d["title"]: d for d in out}
check("480p file below the 720p minimum -> upgrade", by["Movie 1"]["upgrade"] is True)
check("1080p and letterboxed 720p files -> no upgrade", by["Movie 2"]["upgrade"] is False and by["Movie 5"]["upgrade"] is False)
m1, m2, m3 = (film_repo.get(db, ids[k]) for k in ("1", "2", "3"))
check("covered movies are imported with their group", m1["status"] == "imported" and m1["imported_group_id"] == "1" and m2["status"] == "imported")
check("the upgrade flag and the owned height are stored", m1["upgrade_wanted"] == 1 and m1["owned_height"] == 480 and m2["upgrade_wanted"] == 0)
check("a group too short for the movie stays wanted", m3["status"] == "wanted")
check("an empty group, an unknown length and hand-added parts stay wanted",
      all(film_repo.get(db, ids[k])["status"] == "wanted" for k in ("4", "6", "7")))
check("a second run finds nothing new", film_import.adopt_filled_groups(db, Stash(), 720) == [])
check("an empty answer from Stash changes nothing", film_import.adopt_filled_groups(db, EmptyStash(), 720) == [])
no_profile = film_import.adopt_filled_groups(db, Stash(), None, dry_run=True)
check("without a quality profile nothing is an upgrade", all(not d["upgrade"] for d in no_profile))
sys.exit(1 if FAILS else 0)
