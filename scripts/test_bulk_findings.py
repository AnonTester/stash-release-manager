"""Tests (v0.250.1): the bulk Dismiss of the Maintenance findings lists (library check, VR check, duration check, compare groups) and their Undo. No network.
Run:  venv/bin/python scripts/test_bulk_findings.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import routers.maintenance as m  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))
m._db = db
with db.connect() as c:
    c.execute("INSERT INTO library_check_run (id) VALUES (1)") if "library_check_run" in [r[0] for r in c.execute("SELECT name FROM sqlite_master")] else None
    for i, st in enumerate(("pending", "pending", "applied"), 1):
        c.execute("INSERT INTO library_check_finding (run_id, scene_id, scene_title, kind, status) VALUES (1, ?, ?, 'mismatch', ?)", (str(i), f"S{i}", st))
    for i, st in enumerate(("pending", "pending", "dismissed"), 1):
        c.execute("INSERT INTO vr_file_check (scene_id, file_path, verdict, finding_status) VALUES (?, ?, 'mono', ?)", (str(i), f"/p/{i}.mp4", st))
    for i in (1, 2, 3):
        c.execute("INSERT INTO duration_check (scene_id, file_path, provider, provider_scene_id, status) VALUES (?, ?, 'stashdb', ?, 'checked')", (str(i), f"/p/{i}.mp4", f"x{i}"))
def status(sql):
    with db.connect() as conn:
        return [r[0] for r in conn.execute(sql).fetchall()]

out = m.bulk_dismiss_findings({"ids": [1, 2, 3]})
check("library check: only the pending ones are dismissed", out["changed"] == 2 and status("SELECT status FROM library_check_finding ORDER BY id") == ["dismissed", "dismissed", "applied"])
m.bulk_restore_findings({"rows": out["previous"]})
check("... one Undo puts them back", status("SELECT status FROM library_check_finding ORDER BY id") == ["pending", "pending", "applied"])

out = m.bulk_dismiss_vr_findings({"ids": [1, 2, 3]})
check("VR check: the open ones are dismissed, a dismissed one is left", out["changed"] == 2)
m.bulk_restore_vr_findings({"rows": out["previous"]})
check("... Undo restores each to its own status", status("SELECT finding_status FROM vr_file_check ORDER BY rowid") == ["pending", "pending", "dismissed"])

out = m.bulk_ignore_duration({"scene_ids": ["1", "2", "9"]})
check("duration check: two ignored, the unknown one skipped", out["changed"] == 2 and status("SELECT status FROM duration_check ORDER BY scene_id") == ["ignored", "ignored", "checked"])
m.bulk_ignore_duration({"scene_ids": ["1", "2"], "restore": True})
check("... restore brings them back", status("SELECT status FROM duration_check ORDER BY scene_id") == ["checked", "checked", "checked"])

out = m.bulk_ignore_compare_groups({"pairs": [{"folder_key": "k1", "group_id": "g1"}, {"folder_key": "k2", "group_id": "g2"}]})
check("compare groups: two pairs ignored", out["changed"] == 2 and len(status("SELECT group_id FROM group_match_ignore")) == 2)
m.bulk_ignore_compare_groups({"pairs": out["previous"], "undo": True})
check("... Undo takes them back", status("SELECT group_id FROM group_match_ignore") == [])
print("ALL OK" if not FAILS else f"{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
