"""Search all stuck movies (v0.223.1): film_search.stuck_films / search_stuck and the router's preview. THROWAWAY database, fake source.
Run:  venv/bin/python scripts/test_film_stuck.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import film_repo  # noqa: E402
import film_search as fs  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
def add(title, **kw):  # noqa: E302
    fid, _ = film_repo.add(db, title=title, year=2001, tpdb_id="t-" + title, duration_min=90, status="wanted")
    film_repo.update(db, fid, **kw)
    return fid
a = add("Alpha Film", last_search_result="no_match", last_search_at="2026-10-01 10:00:00")
b = add("Beta Film", last_search_result="rejected", last_search_at="2026-10-02 10:00:00")
c = add("Gamma Film", last_search_result="error")
d = add("Delta Film", last_search_result="no_match", review_candidate={"title": "x"})   # a candidate waits: not stuck
e = add("Epsilon Film")                                                                    # never searched: not stuck yet
f = add("Zeta Film", last_search_result="no_match", mode="scenes")                         # put together from scenes: not searched
g = add("Eta Film", last_search_result="part_imported")                                    # waiting for more parts: not stuck
h = add("Theta Film", last_search_result="no_match"); film_repo.update(db, h, status="grabbed")  # grabbed: not stuck

check("stuck = wanted, one-file mode, no candidate, searched with no usable result", {x["id"] for x in fs.stuck_films(db)} == {a, b, c})
check("the longest unsearched first", [x["id"] for x in fs.stuck_films(db)][0] == c)

class Source:
    def __init__(self): self.queries = []
    def search(self, q):
        self.queries.append(q)
        return [{"title": "Alpha.Film.2001.720p.WEB-DL", "size": 2e9}] if "Alpha" in q else []
src = Source()
out = fs.search_stuck(db, src)
check("every stuck movie is searched once", out["searched"] == 3 and {r["id"] for r in out["results"]} == {a, b, c})
check("a plausible release becomes a candidate, the others stay stuck", out["found"] == 1 and film_repo.get(db, a)["review_candidate"]["title"].startswith("Alpha")
      and film_repo.get(db, b)["last_search_result"] == "no_match")
check("the found movie is not stuck any more", {x["id"] for x in fs.stuck_films(db)} == {b, c})
check("nothing is grabbed (status stays wanted)", film_repo.get(db, a)["status"] == "wanted")

from routers import films as fr  # noqa: E402
fr._db = db
pv = fr.stuck_preview()
check("preview: count and an upper bound of the queries (3 per movie)", pv["count"] == 2 and pv["max_queries"] == sum(len(fs.queries(film_repo.get(db, x))) for x in (b, c)))
sys.exit(1 if FAILS else 0)
