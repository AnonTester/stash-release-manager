"""Grab from the search log (v0.231.3): the result is found again by title + indexer + size from the logged queries, a running torrent is
replaced (removed, its hash blocklisted), the very torrent that is downloading is refused, a result the indexers no longer list is a 404."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from fastapi import HTTPException
import routers.releases as R

fails = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

H1, H2 = "a" * 40, "b" * 40
RESULTS = [
    {"title": "Scene.A.1080p", "indexer": "x", "size": 100, "infoHash": H1, "downloadUrl": "http://x/1", "protocol": "torrent"},
    {"title": "Scene.A.720p", "indexer": "x", "size": 80, "infoHash": H2, "downloadUrl": "http://x/2", "protocol": "torrent"},
]

class Source:
    def __init__(self): self.queries = []
    def search(self, q):
        self.queries.append(q)
        return [] if q == "empty" else list(RESULTS)

state = {"details": {"backend": "prowlarr", "queries": [{"query": "empty"}, {"query": "scene a"}]}, "old": None, "grabbed": [], "replaced": []}
src = Source()
_real_details = R.release_repo.get_search_details_for_scene
R.release_repo.get_search_details_for_scene = lambda db, p, s: state["details"]
R._get_manual_search_sources = lambda: {"prowlarr": src}
R.torrent_health.current_for_scene = lambda db, p, s: state["old"]
R.torrent_health.finish_replacement = lambda db, old: state["replaced"].append(old["job_id"])
def fake_grab(db, p, s, cand, replace_job=False):
    state["grabbed"].append((cand["title"], replace_job))
    return {"result": "grabbed", "grab": {"title": cand["title"]}}
R.grab_candidate = fake_grab
R.history.scene = lambda *a, **k: None
R._db = object()

# nothing running: plain grab, the second logged query is tried after the first finds nothing
r = R.last_search_grab("stashdb", "s1", {"title": "Scene.A.720p", "indexer": "x", "size": 80})
check("found through the second query and grabbed", r["grab"] and state["grabbed"] == [("Scene.A.720p", False)] and src.queries == ["empty", "scene a"])
check("nothing replaced without a running torrent", state["replaced"] == [])

# a torrent is running: it is replaced
state["old"] = {"client_id": 1, "job_id": H1}; state["grabbed"].clear()
r = R.last_search_grab("stashdb", "s1", {"title": "Scene.A.720p", "indexer": "x", "size": 80})
check("replace_job set and the old torrent removed", state["grabbed"] == [("Scene.A.720p", True)] and state["replaced"] == [H1])

# the running torrent itself is refused
state["grabbed"].clear(); state["replaced"].clear()
try:
    R.last_search_grab("stashdb", "s1", {"title": "Scene.A.1080p", "indexer": "x", "size": 100}); err = None
except HTTPException as e:
    err = e.status_code
check("the torrent that is downloading is refused", err == 400 and not state["grabbed"])

# gone from the indexers
try:
    R.last_search_grab("stashdb", "s1", {"title": "Scene.Z", "indexer": "x", "size": 1}); err = None
except HTTPException as e:
    err = e.status_code
check("a result no longer listed is a 404", err == 404)

# a torrent the indexers do not list: added from its hash as a magnet (no indexer needed)
state["old"] = None; state["grabbed"].clear()
captured = []
R.grab_candidate = lambda db_, p, s_, cand, replace_job=False: (captured.append(cand), {"result": "grabbed", "grab": {"title": cand["title"]}})[1]
r = R.last_search_grab("stashdb", "s1", {"title": "Scene.Z", "indexer": "x", "size": 1, "info_hash": "c" * 40})
check("not listed but with a hash: grabbed as a magnet", r["grab"] and captured[-1]["downloadUrl"].startswith("magnet:?xt=urn:btih:" + "c" * 40))

# no log
state["details"] = None
try:
    R.last_search_grab("stashdb", "s1", {"title": "Scene.A.720p"}); err = None
except HTTPException as e:
    err = e.status_code
check("no log is a 404", err == 404)

# the search log: up to 500 results are kept, and the release listings do not carry it
import indexer_search as ix
import release_repo
rels = [{"title": f"Some Title {i}", "indexer": "x", "size": 1, "age": 1, "protocol": "usenet", "quality": {"quality": {"resolution": 1080}}} for i in range(620)]
check("the log keeps 500 results", len(ix._explain_candidates(rels, [1080], "Other", "2020-01-01", [], False, [], None, None)) == 500)
check("listings drop last_search_details", release_repo._listed({"title": "t", "last_search_details": "{...}"}) == {"title": "t"})

# on a real database: neither listing returns (or even selects) the log, the on-demand lookup still has it
import tempfile
from db import Database
db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
with db.connect() as c:
    release_repo.insert_release(db, c, {"id": "s1", "title": "T", "date": "2026-10-01", "created": "2026-10-01T00:00:00Z", "studio": {"id": "st", "name": "S"}},
                                "stashdb", "studio", "e1")
with db.connect() as c:
    c.execute("UPDATE release SET last_search_details = ?, last_search_at = '2026-10-01', last_search_result = 'no_match'", ('{"backend": "prowlarr", "candidates": [{"title": "x"}]}',))
listed = release_repo.query_releases(db, statuses=("new",), per_page=0)["rows"]
check("query_releases: row without the log", len(listed) == 1 and "last_search_details" not in listed[0] and listed[0]["last_search_result"] == "no_match")
check("list_releases: row without the log", "last_search_details" not in release_repo.list_releases(db, "new")[0])
check("get_search_details_for_scene still has it", _real_details(db, "stashdb", "s1")["candidates"] == [{"title": "x"}])

print("FAILED" if fails else "all ok")
sys.exit(1 if fails else 0)
