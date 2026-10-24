"""Tests (v0.237.0): bulk actions on Upgrade wanted (dismiss / restore / profile / search cap), "To wishlist" + its Undo, a candidate's own
quality profile in the search, and the upgrades filter of the release query.
Run:  venv/bin/python scripts/test_upgrade_bulk.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from fastapi import HTTPException  # noqa: E402

import quality_profile_repo  # noqa: E402
import release_repo  # noqa: E402
import upgrades  # noqa: E402
from db import Database  # noqa: E402
from routers import maintenance as mt  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))
mt._db = db

def cand(n, reason="vr_low_quality", status="wanted"):
    with db.connect() as conn:
        cur = conn.execute("""INSERT INTO upgrade_candidate (scene_id, file_path, scene_title, provider, provider_scene_id, reason, status,
                              cur_width, cur_height, cur_size) VALUES (?, ?, ?, 'stashdb', ?, ?, ?, 1920, 1080, 1000)""",
                           (str(n), f"/media/f{n}.mp4", f"Scene {n}", f"ps{n}", reason, status))
        return cur.lastrowid
ids = [cand(i) for i in range(1, 4)]
Body = mt.UpgradeBulkBody

# dismiss / restore
r = mt.upgrades_bulk(Body(ids=ids, action="dismiss"))
check("dismiss: all changed, previous returned", r["changed"] == 3 and set(r["previous"].values()) == {"wanted"})
check("dismissed in the list", all(upgrades.get_candidate(db, i)["status"] == "dismissed" for i in ids))
r2 = mt.upgrades_bulk(Body(ids=ids, action="restore", statuses=r["previous"]))
check("restore: back to wanted", r2["restored"] == 3 and all(upgrades.get_candidate(db, i)["status"] == "wanted" for i in ids))
check("restore leaves non-dismissed alone", mt.upgrades_bulk(Body(ids=ids, action="restore"))["restored"] == 0)

# profile
pid = quality_profile_repo.create(db, "HD", [1080], 1080)
pid = pid if isinstance(pid, int) else pid["id"] if isinstance(pid, dict) else pid
r = mt.upgrades_bulk(Body(ids=ids[:2], action="profile", profile_id=pid))
check("profile set on the selected ones only", upgrades.get_candidate(db, ids[0])["quality_profile_id"] == pid
      and upgrades.get_candidate(db, ids[2])["quality_profile_id"] is None)
mt.upgrades_bulk(Body(ids=ids[:1], action="profile", profile_id=None))
check("profile cleared again", upgrades.get_candidate(db, ids[0])["quality_profile_id"] is None)
try:
    mt.upgrades_bulk(Body(ids=ids, action="profile", profile_id=9999)); check("unknown profile refused", False)
except HTTPException as e:
    check("unknown profile refused", e.status_code == 400)

# search cap and results
calls = []
mt._upgrade_search = lambda i, dry_run=False: calls.append(i) or ({"grab": {"title": "x"}, "result": "grabbed"} if i == ids[0] else {"result": "no_quality_match"})
r = mt.upgrades_bulk(Body(ids=ids, action="search"))
check("bulk search runs each once, reports grabs", calls == ids and r["grabbed"] == 1 and len(r["results"]) == 3)
many = [cand(100 + n) for n in range(11)]
try:
    mt.upgrades_bulk(Body(ids=many, action="search")); check("more than 10 searches refused", False)
except HTTPException as e:
    check("more than 10 searches refused", e.status_code == 400)
try:
    mt.upgrades_bulk(Body(ids=[], action="dismiss")); check("empty selection refused", False)
except HTTPException as e:
    check("empty selection refused", e.status_code == 400)

# 'check' candidates: approve, dismiss with undo, wishlist
chk = cand(50, "duration", status="check")
check("a 'check' candidate is not due for the automatic search", chk not in upgrades.due_candidates(db, 50, min_hours=0))
r = mt.upgrades_bulk(Body(ids=[chk], action="approve"))
check("approve: check -> wanted", r["changed"] == 1 and upgrades.get_candidate(db, chk)["status"] == "wanted")
chk2 = cand(51, "duration", status="check")
r = mt.upgrades_bulk(Body(ids=[chk2], action="dismiss"))
check("a 'check' candidate can be dismissed and restored", r["previous"][str(chk2)] == "check"
      and mt.upgrades_bulk(Body(ids=[chk2], action="restore", statuses=r["previous"]))["restored"] == 1
      and upgrades.get_candidate(db, chk2)["status"] == "check")

# migration (once): never-searched wrong-length candidates that exist before the status wait for a look
old_dur = cand(60, "duration", status="wanted")
searched = cand(61, "duration", status="wanted")
upgrades.set_candidate(db, searched, last_search_at="2026-10-01 00:00:00")
with db.connect() as conn:
    conn.execute("DELETE FROM setting WHERE key = 'migrated:upgrade_check_status'")
    db._migrate_upgrade_check_status(conn)
check("migration: a never-searched duration candidate waits for a look, a searched one stays", upgrades.get_candidate(db, old_dur)["status"] == "check"
      and upgrades.get_candidate(db, searched)["status"] == "wanted")
with db.connect() as conn:
    db._migrate_upgrade_check_status(conn)
check("migration runs once", upgrades.get_candidate(db, searched)["status"] == "wanted")

# to wishlist + undo
class Client:
    def get_scene_detail(self, sid): return {"title": "T", "date": "2026-01-01", "studio": {"id": "st", "name": "S"}, "performers": [], "details": ""}
mt._get_provider_client = lambda provider: Client()
r = mt.upgrades_bulk(Body(ids=ids[:1], action="wishlist"))
rows = release_repo.list_rows_for_scene(db, "stashdb", "ps1")
check("to wishlist: a wishlist release row replacing the file", r["changed"] == 1 and len(rows) == 1 and rows[0]["status"] == "sent_to_whisparr"
      and rows[0]["upgrade_of"] == "/media/f1.mp4")
check("candidate is on the wishlist, no longer due for the upgrade search", upgrades.get_candidate(db, ids[0])["status"] == "wishlist"
      and ids[0] not in upgrades.due_candidates(db, 50, min_hours=0))
q = release_repo.query_releases(db, statuses=("sent_to_whisparr",), upgrades="only")
check("release query: only upgrades finds it", q["total"] == 1)
check("release query: hide upgrades hides it", release_repo.query_releases(db, statuses=("sent_to_whisparr",), upgrades="hide")["total"] == 0)
mt.upgrades_bulk(Body(ids=ids[:1], action="unwishlist", previous=r["previous"]))
check("undo: row gone, candidate wanted again", release_repo.list_rows_for_scene(db, "stashdb", "ps1") == []
      and upgrades.get_candidate(db, ids[0])["status"] == "wanted")
bad = cand(7, status="done")
check("a done candidate cannot go to the wishlist", mt.upgrades_bulk(Body(ids=[bad], action="wishlist"))["errors"])

# the candidate's own profile reaches the search
seen = {}
upgrades.search_and_grab = lambda *a, **k: seen.update(profile=a[8]) or {"result": "no_quality_match"}
class Source: available = True
upgrades.set_candidate(db, ids[1], quality_profile_id=pid)
upgrades.search_one(db, ids[1], lambda p: Client(), Source(), {"id": "global"}, False, dry_run=True)
check("own profile used for the search", seen["profile"]["id"] == pid)
upgrades.search_one(db, ids[2], lambda p: Client(), Source(), {"id": "global"}, False, dry_run=True)
check("no own profile: the Settings one", seen["profile"] == {"id": "global"})

print("\nALL OK" if not FAILS else "\nFAILED: %s" % FAILS)
sys.exit(1 if FAILS else 0)
