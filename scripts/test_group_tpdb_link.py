"""Tests (v0.227.0): a Group that lacks the ThePornDB movie link gets it (and its empty fields) from the chosen ThePornDB movie.
Fake Stash, fake ThePornDB, no network.   Run:  venv/bin/python scripts/test_group_tpdb_link.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from library_movies import Movies  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

class FakeStash:
    def __init__(self, group): self.group = group; self.updates = []  # noqa: E702
    def _graphql(self, q, v):
        if "findStudios" in q: return {"findStudios": {"studios": [{"id": "7", "name": "Example Studio", "aliases": []}]}}
        if "findGroup(" in q: return {"findGroup": self.group}
        if "groupUpdate" in q: self.updates.append(v["i"]); return {"groupUpdate": {"id": v["i"]["id"]}}
        raise AssertionError(q)

MOVIE = {"id": "u-1", "title": "Some Film", "slug": "some-film", "date": "2012-03-04", "duration": 5400, "description": "A synopsis.",
         "directors": [{"name": "A. Director"}], "site": {"name": "Example Studio"}, "url": "https://shop.example/some-film", "poster": "https://x/y.jpg"}

def make(group): s = FakeStash(group); return Movies(s, lambda k: ""), s  # noqa: E702, E704

m, s = make({"id": "5", "name": "Some Film", "date": None, "duration": None, "director": None, "synopsis": None, "urls": ["https://www.adultdvdempire.com/1-some-film"], "studio": None})
r = m.link_group_to_tpdb(MOVIE, "5")
u = s.updates[0]
check("the ThePornDB page is added, the shop link stays", "https://theporndb.net/movies/some-film" in u["urls"] and "https://www.adultdvdempire.com/1-some-film" in u["urls"])
check("empty fields are filled", u["date"] == "2012-03-04" and u["duration"] == 5400 and u["director"] == "A. Director" and u["synopsis"] == "A synopsis." and u["studio_id"] == "7")
check("the name and the covers are not touched", "name" not in u and "front_image" not in u and "back_image" not in u)
check("the result lists what was filled", set(r["filled"]) == {"date", "duration", "director", "synopsis", "studio"} and r["link"].endswith("/some-film"))

m, s = make({"id": "5", "name": "Mine", "date": "2011-01-01", "duration": 100, "director": "X", "synopsis": "mine", "urls": ["https://theporndb.net/movies/some-film/"], "studio": {"id": "9"}})
r = m.link_group_to_tpdb(MOVIE, "5")
u = s.updates[0]
check("set fields are never overwritten", not ({"date", "duration", "director", "synopsis", "studio_id"} & set(u)) and r["filled"] == [])
check("an already linked page is not added twice", u["urls"] == ["https://theporndb.net/movies/some-film/"])

m, s = make(None)
try:
    m.link_group_to_tpdb(MOVIE, "5"); ok = False  # noqa: E702
except ValueError: ok = True  # noqa: E701
check("an unknown group is refused", ok)

class Resp:
    def raise_for_status(self): pass  # noqa: E704
    def json(self): return {"data": [{"id": "a", "slug": "t1", "url": "https://shop.example/t1", "title": "T1", "date": "2012-01-01", "duration": 5400, "site": {"name": "S"}, "performers": [1, 2]},
                                        {"id": "b", "title": "T2", "date": None, "duration": None}]}
class Client:
    def _get(self, path, params=None): self.last = (path, params); return Resp()  # noqa: E702
c = Client()
res = Movies.tpdb_search(c, "some film", 90)
check("search: one request, hits within 10 % of the file length are marked", c.last[0] == "/movies" and res[0]["length_ok"] and not res[1]["length_ok"] and res[0]["year"] == 2012 and res[1]["year"] == 0)

check("search: Preview points to the ThePornDB page, not to the shop page", res[0]["url"] == "https://theporndb.net/movies/t1" and res[0]["source_url"] == "https://shop.example/t1"
      and res[1]["url"] is None)

print("\nFAILED: " + ", ".join(FAILS) if FAILS else "\nall ok")
sys.exit(1 if FAILS else 0)
