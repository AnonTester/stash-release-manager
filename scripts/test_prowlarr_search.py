"""Tests (v0.187.0): Prowlarr free-text search no longer sends a category filter (it returned 0 results for XXX releases filed under 6070/6045)
and filters on the result categories instead. Run:  venv/bin/python scripts/test_prowlarr_search.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from prowlarr_client import ProwlarrClient  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

ITEMS = [
    {"title": "a", "categories": [{"id": 6070, "name": "XXX/Other"}, {"id": 106999}]},
    {"title": "b", "categories": [{"id": 6045}, {"id": 106045}]},
    {"title": "c", "categories": [{"id": 6000}]},
    {"title": "audio", "categories": [{"id": 3000, "name": "Audio"}, {"id": 103000}]},
    {"title": "custom-only", "categories": [{"id": 106040}]},
    {"title": "no-cats"},
    {"title": "empty-cats", "categories": []},
]
class Resp:
    def raise_for_status(self): pass  # noqa: E704
    def json(self): return list(ITEMS)  # noqa: E704
class Session:
    calls = []
    def get(self, url, params=None, timeout=None):
        Session.calls.append(params); return Resp()
c = ProwlarrClient("http://prowlarr.invalid", "k")
c.session = Session()

r = c.search("Some Studio")
check("a free-text query sends NO category filter", "categories" not in Session.calls[-1] and Session.calls[-1]["type"] == "search")
check("XXX subcategories are kept (6070, 6045, 6000), Audio and custom-only ids dropped", [x["title"] for x in r if x["title"] in "abc"] == ["a", "b", "c"]
      and "audio" not in [x["title"] for x in r] and "custom-only" not in [x["title"] for x in r])
check("a result without any category is kept", {"no-cats", "empty-cats"} <= {x["title"] for x in r})
r = c.search("")
check("a blank query (RSS pull) still asks for category 6000 and is not filtered", Session.calls[-1]["categories"] == "6000" and len(r) == len(ITEMS))
r = c.search("   ")
check("whitespace counts as blank", "categories" in Session.calls[-1] and len(r) == len(ITEMS))
print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
