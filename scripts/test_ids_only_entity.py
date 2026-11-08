"""Test (v0.253.15): releases_query ids_only carries one watched entity per scene (needed for "Add" of a selection).
Run:  venv/bin/python scripts/test_ids_only_entity.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import release_repo  # noqa: E402
from db import Database  # noqa: E402

db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))
FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

for etype, eid in (("studio", "st1"), ("performer", "pf1")):
    with db.connect() as conn:
        release_repo.insert_release(db, conn, {"id": "s1", "title": "T", "date": "2026-01-01", "created": "2026-01-01", "performers": []}, "stashdb", etype, eid)
res = release_repo.query_releases(db, statuses=("new",), ids_only=True)
check("one scene", res["total"] == 1 and len(res["scenes"]) == 1)
sc = res["scenes"][0]
check("entity fields", sc["entity_type"] in ("studio", "performer") and sc["entity_stashdb_id"] in ("st1", "pf1"))
check("pair belongs together", (sc["entity_type"], sc["entity_stashdb_id"]) in (("studio", "st1"), ("performer", "pf1")))
page = release_repo.query_releases(db, statuses=("new",), page=1, per_page=10)
check("the normal listing is unchanged", len(page["rows"]) == 2)
sys.exit(1 if FAILS else 0)
