"""Test (v0.253.8): a dismissed release remembers the status it had (trigger), and "restore" puts it back there; unknown -> 'new'; hidden-studio rows stay.
Run:  venv/bin/python scripts/test_restore_previous.py"""
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
def add(sid, status):  # noqa: E302
    with db.connect() as conn:
        release_repo.insert_release(db, conn, {"id": sid, "title": sid, "date": "2026-01-01", "created": "2026-01-01", "performers": []},
                                    "stashdb", "studio", "e1", status=status)
def status(sid):  # noqa: E302
    with db.connect() as conn:
        r = conn.execute("SELECT status, dismissed_prev_status FROM release WHERE scene_stashdb_id = ?", (sid,)).fetchone()
    return r["status"], r["dismissed_prev_status"]

add("new1", "new"); add("old1", "backlog"); add("wish1", "sent_to_whisparr")
for sid in ("new1", "old1"):
    release_repo.set_status_for_scene(db, "stashdb", sid, "dismissed")
release_repo.bulk_set_status_for_scenes(db, [("stashdb", "wish1")], "sent_to_whisparr", "dismissed")  # another path: bulk
check("single dismiss remembers new", status("new1") == ("dismissed", "new"))
check("single dismiss remembers backlog", status("old1") == ("dismissed", "backlog"))
check("bulk dismiss remembers the wishlist", status("wish1") == ("dismissed", "sent_to_whisparr"))
check("restore -> backlog", release_repo.restore_previous_for_scene(db, "stashdb", "old1") == 1 and status("old1") == ("backlog", None))
check("restore -> new", release_repo.restore_previous_for_scene(db, "stashdb", "new1") == 1 and status("new1") == ("new", None))
with db.connect() as conn:  # a row dismissed before the column existed
    conn.execute("UPDATE release SET dismissed_prev_status = NULL WHERE scene_stashdb_id = 'wish1'")
check("unknown previous -> new", release_repo.restore_previous_for_scene(db, "stashdb", "wish1") == 1 and status("wish1")[0] == "new")
add("muted1", "new")
with db.connect() as conn:
    conn.execute("UPDATE release SET status = 'dismissed', muted = 1 WHERE scene_stashdb_id = 'muted1'")
check("hidden-studio row is left alone", release_repo.restore_previous_for_scene(db, "stashdb", "muted1") == 0)
check("a second restore changes nothing", release_repo.restore_previous_for_scene(db, "stashdb", "new1") == 0)
sys.exit(1 if FAILS else 0)
