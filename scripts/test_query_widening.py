"""Tests (v0.217.1): the studio-name variant query and the widening that no longer stops at a blocklisted / taken lead. No network, throw-away DB.
Run:  venv/bin/python scripts/test_query_widening.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import indexer_search as ix
import release_repo
from db import Database

FAILS = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

q = ix._build_queries("She Sucks at Golf", ["Gal Ritchie", "Donnie Rock"], "Some Studio: The Series", "2026-10-16")
check("full studio name + date first, then the name before the ':'", "FamilyStrokesTheSeries 26.10.16" in q and "FamilyStrokes 26.10.16" in q and q.index("FamilyStrokesTheSeries 26.10.16") < q.index("FamilyStrokes 26.10.16"))
q = ix._build_queries("T", [], "Plain Studio", "2026-10-16")
check("no ':' -> no extra variant", q.count("PlainStudio 26.10.16") == 1 and len(q) == 2)
q = ix._build_queries("T", [], "Brand - Sub Line", "2026-10-16")
check("' - ' subtitle works the same", "Brand 26.10.16" in q)

DB = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
with DB.connect() as c:
    release_repo.insert_release(DB, c, {"id": "s1", "title": "Unsupervised On Vacation", "created": "2026-09-30T10:00:00Z", "date": "2026-09-30",
                                        "studio": {"id": "st", "name": "Mom Comes First"}}, "stashdb", "studio", "st1")
BLOCKED = "MomComesFirst.26.09.30.Amiee.Cambridge.And.Zarina.Noir.XXX.1080p.MP4-OLD"
GOOD = "MomComesFirst.26.09.30.Amiee.Cambridge.And.Zarina.Noir.XXX.720p.MP4-GRP"
release_repo.add_review_rejected_title(DB, "stashdb", "s1", BLOCKED, exact=True)  # exact: only this name, not its other encodes
check("a blocklisted plausible name is no viable lead",
      not ix._viable_lead([{"title": BLOCKED}], "Unsupervised On Vacation", "2026-09-30", ["Amiee Cambridge", "Zarina Noir"], release_repo.get_review_rejected_titles(DB, "stashdb", "s1"), []))
check("a clean plausible name is",
      ix._viable_lead([{"title": GOOD}], "Unsupervised On Vacation", "2026-09-30", ["Amiee Cambridge", "Zarina Noir"], [], []))
check("a name taken for another scene is no viable lead",
      not ix._viable_lead([{"title": GOOD}], "Unsupervised On Vacation", "2026-09-30", ["Amiee Cambridge", "Zarina Noir"], [], [GOOD]))

class Src:
    name, available = "fake", True
    def __init__(self): self.asked = []
    def search(self, query):
        self.asked.append(query)
        if query == "Unsupervised On Vacation":
            return [{"title": BLOCKED, "downloadUrl": "u1", "size": 2e9, "age": 3, "indexer": "i", "indexerId": 1, "protocol": "usenet", "quality": {"quality": {"resolution": 1080}}}]
        if query == "MomComesFirst 26.09.30":
            return [{"title": GOOD, "downloadUrl": "u2", "size": 1e9, "age": 3, "indexer": "i", "indexerId": 1, "protocol": "usenet", "quality": {"quality": {"resolution": 720}}}]
        return []
    def indexer_priorities(self): return {1: 25}
src = Src()
res = ix.search_and_grab(DB, None, 1, "stashdb", "s1", "Unsupervised On Vacation", "2026-09-30", ["Amiee Cambridge", "Zarina Noir"],
                         {"resolutions": [1080, 720]}, search_source=src, studio_name="Mom Comes First", dry_run=True)
check("the search goes on past the blocklisted hit and finds the other release (the real case)", "MomComesFirst 26.09.30" in src.asked and (res.get("would_grab") or {}).get("title") == GOOD)

print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
