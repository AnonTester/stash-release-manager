"""Tests (v0.250.0): POST /releases/bulk-dismiss dismisses scenes of any open status and hands back the rows for ONE Undo. No network. Run:
venv/bin/python scripts/test_bulk_dismiss.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import events  # noqa: E402
events.publish = lambda *a, **k: None
import release_repo  # noqa: E402
import routers.releases as rr  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))
rr._db = db
def add(i, status):  # noqa: E302
    with db.connect() as conn:
        release_repo.insert_release(db, conn, {"id": f"s{i}", "title": f"Scene {i}", "date": "2026-10-01", "studio": {"id": "st", "name": "St"}, "performers": [],
                                               "created": "2026-10-01T00:00:00Z"}, "stashdb", "studio", "st", status=status)
for i, st in ((1, "new"), (2, "sent_to_whisparr"), (3, "import_failed"), (4, "dismissed")):
    add(i, st)
req = rr.BulkScenesRequest(scenes=[rr.BulkScene(provider="stashdb", scene_stashdb_id=f"s{i}") for i in (1, 2, 3, 4, 9)])
out = rr.bulk_dismiss(req)
check("three open scenes were dismissed (an already dismissed and an unknown one are skipped)", out["changed"] == 3)
status = lambda i: release_repo.snapshot_scene_rows(db, "stashdb", f"s{i}")  # noqa: E731 -- rows that are NOT dismissed
check("nothing open is left of them", all(not status(i) for i in (1, 2, 3)))
check("the answer carries the rows as they were", sorted(r["status"] for r in out["previous"]) == ["import_failed", "new", "sent_to_whisparr"])
rr.restore_dismissed(rr.RestoreRowsRequest(rows=[rr.RestoreRow(**{k: r[k] for k in ("provider", "scene_stashdb_id", "entity_type", "entity_stashdb_id", "status")}) for r in out["previous"]]))
check("one Undo puts every row back to its OWN status", sorted(r["status"] for i in (1, 2, 3) for r in status(i)) == ["import_failed", "new", "sent_to_whisparr"])
# the body exactly as the page sends it (no "action"): FastAPI validates it with this model -- a model that asked for more answered HTTP 422
body = rr.BulkScenesRequest.model_validate({"scenes": [{"provider": "stashdb", "scene_stashdb_id": "s1"}]})
check("the request body of the page is accepted (no 'action' field)", len(body.scenes) == 1)
print("ALL OK" if not FAILS else f"{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
