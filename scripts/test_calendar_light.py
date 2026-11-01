"""Tests (v0.249.8): the light month query of the Calendar carries only the fields that decide a card's look, and the same signature as the full rows would.
No network, throw-away DB. Run:  venv/bin/python scripts/test_calendar_light.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import release_repo  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))
db.init() if hasattr(db, "init") else None
with db.connect() as conn:
    release_repo.insert_release(db, conn, {"id": "s1", "title": "Scene One", "date": "2026-10-05", "studio": {"id": "st", "name": "Studio"}, "performers": [],
                                           "created": "2026-10-05T00:00:00Z", "details": "a long description " * 50}, "stashdb", "studio", "st", status="new")
args = dict(statuses=("new",), date_from="2026-10-01", date_until="2026-10-31", per_page=0)
full = release_repo.query_releases(db, **args)["rows"]
light = release_repo.query_releases(db, light=True, **args)["rows"]
check("one row each", len(full) == 1 and len(light) == 1)
check("the light row has only the keys that decide the look", set(light[0]) <= set(release_repo.LIGHT_KEYS) and "details" not in light[0] and "title" not in light[0])
sig = lambda rows: [[r.get(k) or "" for k in release_repo.LIGHT_KEYS] for r in rows]  # noqa: E731
check("the same values as the full row", sig(light) == sig(full))
check("the light answer is much smaller", len(str(light)) * 3 < len(str(full)))
print("ALL OK" if not FAILS else f"{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
