"""Tests (v0.250.8): upgrade rows (release.upgrade_of) that the pre-grab library check marked 'imported' go back on the wishlist; real imports stay.
Run:  venv/bin/python scripts/test_upgrade_rows_restore.py"""
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

def add(sid, status, upgrade_of=None, job=None, grabbed_title=None, cand=None):
    scene = {"id": sid, "title": sid, "date": "2020-01-01", "created": "2020-01-02T00:00:00Z", "studio": {"name": "S"}}
    with db.connect() as conn:
        release_repo.insert_release(db, conn, scene, "stashdb", "studio", "st1", status=status)
        conn.execute("UPDATE release SET upgrade_of = ?, download_job_id = ?, grabbed_title = ?, imported_at = datetime('now') WHERE scene_stashdb_id = ?",
                     (upgrade_of, job, grabbed_title, sid))
        if cand:
            conn.execute("INSERT INTO upgrade_candidate (scene_id, file_path, scene_title, provider, provider_scene_id, reason, status) "
                         "VALUES (?, ?, ?, 'stashdb', ?, 'duration', ?)", (f"L{sid}", upgrade_of or "/x.mp4", sid, sid, cand))

add("wrongly", "imported", upgrade_of="/lib/a.mp4", cand="wishlist")          # marked imported by the old library check
add("done", "imported", upgrade_of="/lib/b.mp4", cand="done")                 # a real, finished upgrade
add("grabbed", "imported", upgrade_of="/lib/c.mp4", cand="wishlist", job="h1", grabbed_title="T")  # something was downloaded for it
add("plain", "imported")                                                      # a normal import
add("nocand", "imported", upgrade_of="/lib/d.mp4")                            # no candidate on the wishlist

check("one row restored", release_repo.restore_upgrade_rows_marked_imported(db) == 1)
st = lambda sid: release_repo.list_rows_for_scene(db, "stashdb", sid)[0]
check("the wrongly imported upgrade is on the wishlist again", st("wrongly")["status"] == "sent_to_whisparr" and st("wrongly")["imported_at"] is None)
check("a finished upgrade stays imported", st("done")["status"] == "imported")
check("an upgrade with a download stays", st("grabbed")["status"] == "imported")
check("a normal import stays", st("plain")["status"] == "imported")
check("an upgrade row without a wishlist candidate stays", st("nocand")["status"] == "imported")
check("a second run changes nothing", release_repo.restore_upgrade_rows_marked_imported(db) == 0)
print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
