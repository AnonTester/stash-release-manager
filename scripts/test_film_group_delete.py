"""Tests of "Dismiss & delete group" (v0.168.0): film_groups.delete_check/delete_group and the router endpoint against a THROWAWAY database, a
throwaway data dir and a faked Stash (nothing real is touched). Run:  venv/bin/python scripts/test_film_group_delete.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import film_groups  # noqa: E402
import film_repo  # noqa: E402
from db import Database  # noqa: E402
from routers import films as films_router  # noqa: E402
from fastapi import HTTPException  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)


class Stash:
    def __init__(self, groups):
        self.groups, self.destroyed = groups, []
        self.session = self
    def get(self, *a, **k): raise OSError("no image in the test")
    def _graphql(self, query, variables):
        gid = variables.get("id") or (variables.get("i") or {}).get("id")
        if "groupDestroy" in query:
            self.destroyed.append(gid); return {"groupDestroy": True}
        g = self.groups.get(str(gid))
        if "front_image_path" in query:
            return {"findGroup": g and {"name": g["name"], "date": "2011-01-01", "duration": 0, "director": "", "synopsis": "", "studio": None, "urls": [],
                                        "front_image_path": None, "back_image_path": None}}
        return {"findGroup": g}


empty = {"id": "1", "name": "Empty One", "scenes": []}
nofile = {"id": "2", "name": "Scene Without File", "scenes": [{"id": "9", "title": "t", "files": []}]}
full = {"id": "3", "name": "Has Files", "scenes": [{"id": "8", "title": "t", "files": [{"path": "/data/x/a.mp4"}]}]}
stash = Stash({"1": empty, "2": nofile, "3": full})
out = tempfile.mkdtemp()

c = film_groups.delete_check(stash, 3)
check("check: files counted with a path", c["exists"] and c["files"] == 1 and c["file_list"] == ["/data/x/a.mp4"])
check("check: missing group", film_groups.delete_check(stash, 99) == {"exists": False})
r = film_groups.delete_group(stash, 3, out)
check("group with files: refused without force", r.get("needs_force") and not stash.destroyed)
check("group with files: nothing written", not os.listdir(out))
r = film_groups.delete_group(stash, 1, out)
check("empty group: deleted, backup written", r.get("ok") and stash.destroyed == ["1"] and os.path.exists(os.path.join(out, r["backup"])))
check("backup holds the name", json.load(open(os.path.join(out, r["backup"])))["name"] == "Empty One")
r = film_groups.delete_group(stash, 3, out, force=True)
check("group with files: deleted with force", r.get("ok") and stash.destroyed == ["1", "3"] and r["files"] == 1)
check("missing group: error, no delete", "error" in film_groups.delete_group(stash, 99, out) and len(stash.destroyed) == 2)

# router
db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
films_router._db, films_router._data_dir = db, out
stash2 = Stash({"1": dict(empty), "3": dict(full)})
films_router._get_stash_client = lambda: stash2
fid, _ = film_repo.add(db, title="Empty One", year=2011, source="empty-group", stash_group_id=1, status="wanted")
fid2, _ = film_repo.add(db, title="Manual", year=2012, source="manual", status="wanted")
fid3, _ = film_repo.add(db, title="Has Files", year=2013, source="empty-group", stash_group_id=3, status="wanted")
B = films_router.DismissDeleteBody
r = films_router.dismiss_delete_group(fid, B())
check("router dry run: nothing deleted, nothing dismissed", r["dry_run"] and not stash2.destroyed and film_repo.get(db, fid)["status"] == "wanted")
try:
    films_router.dismiss_delete_group(fid2, B(dry_run=False)); check("router: a movie not from an empty group is refused", False)
except HTTPException as e:
    check("router: a movie not from an empty group is refused", e.status_code == 400 and not stash2.destroyed)
r = films_router.dismiss_delete_group(fid3, B(dry_run=False))
check("router: group with files is refused, movie stays wanted", r.get("needs_force") and film_repo.get(db, fid3)["status"] == "wanted" and not stash2.destroyed)
r = films_router.dismiss_delete_group(fid, B(dry_run=False))
check("router: group deleted and movie dismissed", r.get("ok") and stash2.destroyed == ["1"] and film_repo.get(db, fid)["status"] == "dismissed" and r["previous"] == "wanted")
r = films_router.dismiss_delete_group(fid3, B(dry_run=False, force=True))
check("router: with force the group with files goes too", r.get("ok") and stash2.destroyed == ["1", "3"] and film_repo.get(db, fid3)["status"] == "dismissed")
print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
