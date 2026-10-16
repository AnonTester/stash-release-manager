"""fill-cast (v0.213.7): reads the cast of wishlist scenes that have none stored. Container: PYTHONPATH=/app python3 this.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from db import Database
import release_repo
from routers import releases

res = []
def check(name, cond):
    res.append(bool(cond)); print(("ok   " if cond else "FAIL ") + name)

class Sdb:
    def __init__(self): self.asked = []
    def get_scenes_cast_batch(self, ids):
        self.asked.append(list(ids))
        return {"s1": [{"performer": {"name": "Ada Lapiedra", "gender": "FEMALE"}}, {"performer": {"name": "Lia Lin", "gender": "FEMALE"}}]}  # s2 unknown

db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
with db.connect() as c:
    for sid in ("s1", "s2", "s3"):
        release_repo.insert_release(db, c, {"id": sid, "title": sid, "date": "2026-10-01", "created": "2026-10-01T00:00:00Z"}, "stashdb", "studio", "e1")
release_repo.set_pairing_for_scene(db, "stashdb", "s3", "G/G", "|Known Name|")
releases._db = db
sdb = Sdb(); releases._get_stashdb_client = lambda: sdb
out = releases.fill_cast({"scenes": [{"provider": "stashdb", "scene_stashdb_id": x} for x in ("s1", "s2", "s3", "nope")]})["cast"]
check("one batch for the scenes without a cast", sdb.asked == [["s1", "s2"]])
check("names stored and returned", out["stashdb:s1"]["cast_names"] == "|Ada Lapiedra|Lia Lin|" and out["stashdb:s1"]["cast_pairing"] == "G/G")
check("a scene the provider does not know is '' (not asked again)", out["stashdb:s2"]["cast_names"] == "")
check("an already known cast is returned without a request", out["stashdb:s3"]["cast_names"] == "|Known Name|")
check("cast links are returned (name, local performer id or None)", out["stashdb:s1"]["cast"] == [{"name": "Ada Lapiedra", "local_id": None}, {"name": "Lia Lin", "local_id": None}])
releases._lookup_cache["performer"] = ({}, {"adalapiedra": 42})
releases._lookup_cache["at"] = __import__("time").time()
rows = releases._add_studio_local_ids({"rows": [{"entity_type": "studio", "studio_name": "S", "cast_names": "|Ada Lapiedra|Lia Lin|", "entity_local_id": 1}]})["rows"]
check("query rows carry linked cast (local id by name)", rows[0]["cast"] == [{"name": "Ada Lapiedra", "local_id": 42}, {"name": "Lia Lin", "local_id": None}])
check("a scene that is not in the list at all is skipped", "stashdb:nope" not in out)
sdb.asked.clear()
releases.fill_cast({"scenes": [{"provider": "stashdb", "scene_stashdb_id": "s1"}, {"provider": "stashdb", "scene_stashdb_id": "s2"}]})
check("second call: nothing to read any more", sdb.asked == [])
print("\nALL OK" if all(res) else "\nFAILED"); sys.exit(0 if all(res) else 1)
