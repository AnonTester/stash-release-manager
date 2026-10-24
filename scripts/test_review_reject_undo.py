"""Tests (v0.236.3): Undo of "Not this one" / Reject on a possible match -- the title leaves the blocklist, the candidate is back, the scene is
"needs review" again. Run:  venv/bin/python scripts/test_review_reject_undo.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from fastapi import HTTPException  # noqa: E402

import release_repo  # noqa: E402
from db import Database  # noqa: E402
from routers import releases as rel  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))
rel._db = db
with db.connect() as conn:
    for ent in ("e1", "e2"):  # two rows for one scene (two watched entities)
        release_repo.insert_release(db, conn, {"id": "s1", "title": "T", "date": "2026-01-01", "created": "2026-01-01", "performers": []},
                                    "stashdb", "studio", ent, status="sent_to_whisparr")
CAND = {"title": "Some.Release.1080p", "indexer": "X", "downloadUrl": "http://x/dl?apikey=SECRET", "size": 1000, "age": 15}
release_repo.set_review_candidate_for_scene(db, "stashdb", "s1", CAND)
release_repo.set_search_result_for_scene(db, "stashdb", "s1", "needs_review")

r = rel.reject_review_candidate("stashdb", "s1")
check("reject answers ok without the candidate (it carries a download link)", r == {"ok": True})
rows = release_repo.list_rows_for_scene(db, "stashdb", "s1")
check("rejected: blocklisted on every row, candidate gone, search result changed",
      all("Some.Release.1080p" in release_repo.get_review_rejected_titles(db, "stashdb", "s1") for _ in rows)
      and release_repo.get_review_candidate_for_scene(db, "stashdb", "s1") is None and rows[0]["last_search_result"] != "needs_review")

r = rel.restore_review_candidate("stashdb", "s1")
rows = release_repo.list_rows_for_scene(db, "stashdb", "s1")
check("undo answers with the title", r["ok"] and r["title"] == "Some.Release.1080p")
check("undo: off the blocklist (all rows), candidate back with its link, needs review again",
      all("Some.Release.1080p" not in json_list for json_list in [release_repo.get_review_rejected_titles(db, "stashdb", "s1")])
      and all(not (x["review_rejected_titles"] and "Some.Release" in x["review_rejected_titles"]) for x in rows)
      and release_repo.get_review_candidate_for_scene(db, "stashdb", "s1") == CAND and rows[0]["last_search_result"] == "needs_review")

try:
    rel.restore_review_candidate("stashdb", "s1"); check("a second undo is refused", False)
except HTTPException as e:
    check("a second undo is refused", e.status_code == 404)

# another title on the blocklist stays
release_repo.add_review_rejected_title(db, "stashdb", "s1", "Other.Release")  # (this also clears the stored candidate)
release_repo.set_review_candidate_for_scene(db, "stashdb", "s1", CAND)
rel.reject_review_candidate("stashdb", "s1")
rel.restore_review_candidate("stashdb", "s1")
check("other blocklist entries stay", release_repo.get_review_rejected_titles(db, "stashdb", "s1") == ["Other.Release"])

print("\nALL OK" if not FAILS else "\nFAILED: %s" % FAILS)
sys.exit(1 if FAILS else 0)
