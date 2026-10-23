"""Tests (v0.233.2): release_repo.wishlist_states(include_open=True) reports scenes found by a scan (new/backlog/late) as 'known'.
Run:  venv/bin/python scripts/test_wishlist_states_known.py"""
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
def scene(i, status, ent="e1"):
    return (i, status, ent)
with db.connect() as conn:
    for sid, status, ent in (scene("n", "new"), scene("b", "backlog"), scene("w", "sent_to_whisparr"),
                             scene("m", "new", "e1"), scene("m", "imported", "e2"), scene("x", "dismissed")):
        release_repo.insert_release(db, conn, {"id": sid, "title": "T " + sid, "date": "2026-01-01", "created": "2026-01-01", "performers": []}, "stashdb",
                                    "studio", ent, status=status)
plain = release_repo.wishlist_states(db, "stashdb", ["n", "b", "w", "m", "x", "zz"])
check("default: open rows are left out", plain == {"w": "sent_to_whisparr", "m": "imported", "x": "dismissed"})
full = release_repo.wishlist_states(db, "stashdb", ["n", "b", "w", "m", "x", "zz"], include_open=True)
check("include_open: new/backlog are known", full["n"] == "known" and full["b"] == "known")
check("a real state beats known", full["m"] == "imported" and full["w"] == "sent_to_whisparr" and full["x"] == "dismissed")
check("unseen scene absent", "zz" not in full)
print("\nALL OK" if not FAILS else "\nFAILED: %s" % FAILS)
sys.exit(1 if FAILS else 0)
