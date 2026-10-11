"""Tests (v0.186.0): "Add to Wishlist" when Whisparr's metadata does not know the scene yet (found live: HTTP 500). No network, throw-away DB.
Run:  venv/bin/python scripts/test_handoff_lookup.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import handoff  # noqa: E402
import release_repo  # noqa: E402
from db import Database  # noqa: E402
from whisparr_client import WhisparrLookupEmpty  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

DB = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
with DB.connect() as c:
    release_repo.insert_release(DB, c, {"id": "s1", "title": "T", "created": "2026-10-09T10:00:00Z", "date": "2026-10-09"}, "stashdb", "performer", "p1")
with DB.connect() as c:  # a configured Whisparr (without it the scope is always 'no_whisparr' and nothing of this is reached)
    c.execute("INSERT OR REPLACE INTO setting (key, value) VALUES ('whisparr_url', 'http://whisparr.invalid')")
    c.execute("INSERT OR REPLACE INTO setting (key, value) VALUES ('whisparr_api_key', 'k')")
def scope(v):  # noqa: E302
    with DB.connect() as c:
        c.execute("INSERT OR REPLACE INTO setting (key, value) VALUES ('whisparr_scope_preset', ?)", (v,))
def status():  # noqa: E302
    with DB.connect() as c:
        return c.execute("SELECT status FROM release WHERE scene_stashdb_id = 's1'").fetchone()[0]

class Whisparr:
    def lookup_by_stash_id(self, sid): raise WhisparrLookupEmpty("no metadata")  # noqa: E704
    def find_by_stash_id(self, sid): return None  # noqa: E704
SEARCHES = []
handoff.attempt_sidecar_search = lambda *a, **k: (SEARCHES.append(1), {"grab": None, "result": "no_results"})[1]
ARGS = (DB, Whisparr(), "stashdb", "s1", "performer", "p1", 1, 1, "/data/", None, False)

scope("bookkeeping_only")
r = handoff.hand_off(*ARGS)
check("bookkeeping_only: no crash, the sidecar searched", r["movie"] is None and r["search_result"] == "no_results" and len(SEARCHES) == 1)
check("the release is on the wishlist", status() == "sent_to_whisparr")
r = handoff.hand_off(*ARGS)
check("'Search now' on the same release works too (no 500 on a missing Whisparr record)", r["already_sent"] is True and len(SEARCHES) == 2)

with DB.connect() as c:
    c.execute("UPDATE release SET status = 'new'")
for preset in ("search_sidecar_import", "full_whisparr"):
    scope(preset)
    try:
        handoff.hand_off(*ARGS); msg = None
    except ValueError as e:
        msg = str(e)
    check(f"{preset}: a clear message instead of a crash", msg is not None and "no metadata" in msg and status() == "new")
check("the sidecar did not search under those presets", len(SEARCHES) == 2)

print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
