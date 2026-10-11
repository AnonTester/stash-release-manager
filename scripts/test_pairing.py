"""Tests (v0.182.0): scene pairing (cast by gender) -- the code, storing it on insert, the on-demand read for older rows, the filter options.
No network, throw-away database. Run:  venv/bin/python scripts/test_pairing.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import release_repo as rr  # noqa: E402
from db import Database  # noqa: E402
from routers import backlog  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

P = lambda *g: [{"performer": {"gender": x}} for x in g]  # noqa: E731
check("not fetched -> None", rr.pairing_code(None) is None)
check("no cast -> empty", rr.pairing_code([]) == "")
check("solo girl", rr.pairing_code(P("FEMALE")) == "G")
check("solo guy", rr.pairing_code(P("MALE")) == "B")
check("boy girl, whatever the order", rr.pairing_code(P("FEMALE", "MALE")) == "B/G" == rr.pairing_code(P("MALE", "FEMALE")))
check("girl girl", rr.pairing_code(P("FEMALE", "FEMALE")) == "G/G")
check("threesome B/G/G", rr.pairing_code(P("FEMALE", "MALE", "FEMALE")) == "B/G/G")
check("trans and non-binary count as T", rr.pairing_code(P("TRANSGENDER_FEMALE", "NON_BINARY", "MALE")) == "B/T/T")
check("unknown gender -> ?", rr.pairing_code(P(None, "FEMALE")) == "G/?")
check("five or more = Group", rr.pairing_code(P("FEMALE", "FEMALE", "MALE", "MALE", "FEMALE")) == "Group")
N = lambda *n: [{"performer": {"name": x}} for x in n]  # noqa: E731
check("names: not fetched -> None, no cast -> ''", rr.names_text(None) is None and rr.names_text([]) == "")
check("names: '|A|B|', duplicates and a | inside a name handled", rr.names_text(N("Anna", "Bo|b", "Anna")) == "|Anna|Bo b|")
check("plain dicts work too", rr.pairing_code([{"gender": "FEMALE"}, {"gender": "FEMALE"}]) == "G/G")

DB = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
def scene(i, perfs): return {"id": i, "title": f"t{i}", "created": "2026-09-15", "date": f"2026-09-{10 + int(i[1:])}", **({"performers": perfs} if perfs is not None else {})}  # noqa: E704
with DB.connect() as c:
    rr.insert_release(DB, c, scene("s1", [{"performer": {"gender": "FEMALE", "name": "Ann"}}, {"performer": {"gender": "MALE", "name": "Ben"}}]), "stashdb", "performer", "p1", status="backlog")
    rr.insert_release(DB, c, scene("s2", None), "stashdb", "performer", "p1", status="backlog")
    rr.insert_release(DB, c, scene("s3", None), "stashdb", "performer", "p1", status="dismissed")
    rr.insert_release(DB, c, scene("s4", None), "stashdb", "performer", "p2", status="backlog")
with DB.connect() as c:
    got = {r["scene_stashdb_id"]: r["cast_pairing"] for r in c.execute("SELECT scene_stashdb_id, cast_pairing FROM release")}
check("stored on insert", got["s1"] == "B/G")
with DB.connect() as c:
    check("names stored on insert", c.execute("SELECT cast_names FROM release WHERE scene_stashdb_id='s1'").fetchone()[0] == "|Ann|Ben|")
check("rows without a cast list stay NULL", got["s2"] is None and got["s4"] is None)
ST = ("backlog", "sent_to_whisparr", "import_failed")
check("missing list: only this entity, only the panel's statuses", rr.list_scenes_missing_pairing(DB, "stashdb", "performer", "p1", ST) == ["s2"])

class Client:
    calls = []
    def get_scenes_cast_batch(self, ids):
        Client.calls.append(list(ids))
        return {"s2": [{"performer": {"gender": "FEMALE", "name": "Cy"}}, {"performer": {"gender": "FEMALE", "name": "Di"}}]}  # s5 would be unknown to StashDB
backlog._db = DB
backlog._get_stashdb_client = lambda: Client()
r = backlog.pairings("stashdb", "performer", "p1")
check("endpoint reads and reports", r == {"filled": 1, "remaining": 0} and Client.calls == [["s2"]])
with DB.connect() as c:
    check("endpoint stored the names", c.execute("SELECT cast_names FROM release WHERE scene_stashdb_id='s2'").fetchone()[0] == "|Cy|Di|")
with DB.connect() as c:
    c.execute("UPDATE release SET cast_names = NULL WHERE scene_stashdb_id = 's1'")
check("a row with a pairing but no names is read again", rr.list_scenes_missing_pairing(DB, "stashdb", "performer", "p1", ST) == ["s1"])
with DB.connect() as c:
    c.execute("UPDATE release SET cast_names = '|Ann|Ben|' WHERE scene_stashdb_id = 's1'")
with DB.connect() as c:
    check("endpoint stored G/G", c.execute("SELECT cast_pairing FROM release WHERE scene_stashdb_id='s2'").fetchone()[0] == "G/G")
    check("other entity untouched", c.execute("SELECT cast_pairing FROM release WHERE scene_stashdb_id='s4'").fetchone()[0] is None)
check("nothing left -> no request", backlog.pairings("stashdb", "performer", "p1") == {"filled": 0, "remaining": 0} and len(Client.calls) == 1)

with DB.connect() as c:
    rr.insert_release(DB, c, scene("s6", P("MALE")), "stashdb", "performer", "p3", status="backlog")
class Gone:
    def get_scenes_cast_batch(self, ids): return {}
with DB.connect() as c:
    rr.insert_release(DB, c, scene("s7", None), "stashdb", "performer", "p3", status="backlog")
backlog._get_stashdb_client = lambda: Gone()
backlog.pairings("stashdb", "performer", "p3")
with DB.connect() as c:
    check("scene StashDB no longer knows -> '' (not asked again)", c.execute("SELECT cast_pairing FROM release WHERE scene_stashdb_id='s7'").fetchone()[0] == "")
try:
    backlog.pairings("theporndb", "performer", "p1")
    refused = False
except Exception:
    refused = True
check("a non-StashDB provider is refused", refused)

print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
