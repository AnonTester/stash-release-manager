"""Tests (v0.218.3): releases dated one day off are looked for and offered for REVIEW. Run: venv/bin/python scripts/test_near_date.py"""
import os, sys, tempfile
from datetime import date, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import indexer_search as ix
import release_repo, quality_profile_repo
from db import Database

FAILS = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

NAMES = ["Layla Belle"]
UHD = "SomeStudio.26.09.26.Layla.XXX.2160p.MP4-P2P"
check("studio + one day off + nothing unexplained -> near-date match", ix._near_date_studio_match("Layla", "2026-09-25", NAMES, "Some Studio", UHD))
check("the exact day is not this rule's business (delta 0 still passes the date test)", ix._release_date_delta("2026-09-26", UHD) == 0)
check("another studio's release one day off is no match", not ix._near_date_studio_match("Layla", "2026-09-25", NAMES, "Some Studio", "Private.26.09.26.Layla.XXX.2160p.MP4-GRP"))
check("two days off is no match", not ix._near_date_studio_match("Layla", "2026-09-24", NAMES, "Some Studio", UHD))
check("a name the scene does not explain is no match", not ix._near_date_studio_match("Layla", "2026-09-25", NAMES, "Some Studio", "SomeStudio.26.09.26.Candy.Layla.XXX.2160p.MP4-P2P"))
check("no date in the release name is no match", not ix._near_date_studio_match("Layla", "2026-09-25", NAMES, "Some Studio", "SomeStudio.Layla.XXX.2160p.MP4-P2P"))
check("the studio's subtitle spelling works", ix._near_date_studio_match("Golf", "2026-10-15", ["Gal Ritchie"], "Some Studio: The Series", "FamilyStrokes.26.10.16.Golf.XXX.1080p.MP4-GRP"))
q = ix._build_queries("Layla", NAMES, "Some Studio", "2026-09-25")
check("queries: exact day first, then the day before and after", q.index("SomeStudio 26.09.25") < q.index("SomeStudio 26.09.24") and "SomeStudio 26.09.26" in q)

DB = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
pid = quality_profile_repo.create(DB, "Std", [1080, 720], None)
with DB.connect() as c:
    c.execute("INSERT INTO setting (key, value) VALUES ('quality_profile_id', ?)", (str(pid),))
profile = quality_profile_repo.get_one(DB, pid)
old = (date(2026, 9, 25))
def run(scene_day, sid):
    d = scene_day.isoformat()
    with DB.connect() as c:
        release_repo.insert_release(DB, c, {"id": sid, "title": "Layla", "created": d + "T10:00:00Z", "date": d, "studio": {"id": "st", "name": "Some Studio"}}, "stashdb", "studio", "st1")
        c.execute("UPDATE release SET status='sent_to_whisparr' WHERE scene_stashdb_id = ?", (sid,))
    class Src:
        name, available = "fake", True
        def search(self, q):
            nd = scene_day + timedelta(days=1)
            t = f"SomeStudio.{nd:%y.%m.%d}.Layla.XXX.2160p.MP4-P2P"
            return [{"title": t, "downloadUrl": "u", "size": 1.4e9, "age": 20, "indexer": "i", "indexerId": 1, "protocol": "usenet", "quality": {"quality": {"resolution": 2160}}}]
        def indexer_priorities(self): return {1: 25}
    return ix.search_and_grab(DB, None, 1, "stashdb", sid, "Layla", d, NAMES, profile, search_source=Src(), studio_name="Some Studio", dry_run=True), Src
res, Src = run(date.today() - timedelta(days=22), "old1")
rev = [c for c in res.get("candidates", []) if c["verdict"] == "review"]
check("an old scene: the 2160p release a day off is offered for review (not grabbed)", res["result"] == "needs_review" and len(rev) == 1 and not res.get("would_grab"))
res2, _ = run(date.today() - timedelta(days=3), "young1")
check("a young scene: the 2160p release is not offered (the profile does not take it yet)", res2["result"] != "needs_review")

print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
