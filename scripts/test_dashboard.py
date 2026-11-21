"""Dashboard endpoints (v0.261.0): activity counts and the calendar."""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from db import Database
import release_repo
from routers import dashboard

fails = []
def check(n, ok):
    print(("ok   " if ok else "FAIL ") + n)
    if not ok: fails.append(n)

import history
db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3")); history.init(db)
def add(i, status, date, title="T", job=None, vr=0, ent="E1"):
    with db.connect() as c:
        c.execute("INSERT INTO release (provider, scene_stashdb_id, entity_type, entity_stashdb_id, title, scene_date, scene_created, studio_name, status, download_job_id, is_vr, import_error_code) "
                  "VALUES ('stashdb', ?, 'studio', ?, ?, ?, '2026-01-01T00:00:00Z', 'Studio', ?, ?, ?, ?)",
                  (i, ent, title + i, date, status, job, vr, "fingerprint_mismatch" if status == "import_failed" else None))
add("a", "imported", "2026-11-20"); add("b", "sent_to_whisparr", "2026-11-21"); add("c", "sent_to_whisparr", "2026-11-21", job="j")
add("d", "import_failed", "2026-11-22"); add("e", "new", "2026-11-22"); add("b", "sent_to_whisparr", "2026-11-21", ent="E2")
add("f", "imported", "2027-05-01")
dashboard.init_dashboard_router(db, lambda: {"c": {"phase": "downloading", "percentage": "42", "timeleft": "0:10:00", "mb": "100", "mbleft": "58"},
                                              "x": {"phase": "verifying"}}, lambda: {"wishlist": [{"scene_stashdb_id": "b", "provider": "stashdb", "title": "Tb", "review_candidate": {"title": "Cand"}}], "upgrades": []})
a = dashboard.activity()
check("counts", a["counts"] == {"downloading": 1, "importing": 1, "needs_review": 1, "failed": 1})
check("download numbers", a["downloading"][0]["percentage"] == 42.0 and a["downloading"][0]["title"] == "Tc")
check("review candidate named", a["needs_review"][0]["candidate"] == "Cand")
check("failed carries the code", a["failed"][0]["error_code"] == "fingerprint_mismatch")
c = dashboard.calendar("2026-11-20", "2026-11-22")
check("default = wishlist + library, a scene once", c["total"] == 4 and [d["date"] for d in c["days"]] == ["2026-11-20", "2026-11-21", "2026-11-22"])
st = {i["id"]: i["state"] for i in c["items"]}
check("states", st == {"a": "downloaded", "b": "wanted", "c": "downloading", "d": "failed"})
check("available only on request", dashboard.calendar("2026-11-20", "2026-11-22", include_available=True)["total"] == 5)
try:
    dashboard.calendar("2026-01-01", "2028-01-01"); check("range limit", False)
except Exception:
    check("range limit", True)
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
