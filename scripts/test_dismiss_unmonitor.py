"""Dismiss of a wishlist release unmonitors the movie in Whisparr (v0.213.5). Run in the container: PYTHONPATH=/app python3 this.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from db import Database
import release_repo
from routers import releases

res = []
def check(name, cond):
    res.append(bool(cond)); print(("ok   " if cond else "FAIL ") + name)

class Whisparr:
    def __init__(self, movie): self.movie, self.calls = movie, []
    def find_by_stash_id(self, sid): return self.movie
    def set_movie_monitored(self, mid, mon): self.calls.append((mid, mon))

db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
scene = {"id": "s1", "title": "T", "date": "2026-10-01", "created": "2026-10-01T00:00:00Z"}
with db.connect() as c:
    release_repo.insert_release(db, c, scene, "stashdb", "studio", "e1")
    c.execute("UPDATE release SET status='sent_to_whisparr'")
    release_repo.insert_release(db, c, {**scene, "id": "s2"}, "stashdb", "studio", "e1")  # status new
releases._db = db
releases.uses_whisparr = lambda d, p: True

w = Whisparr({"id": 7, "monitored": True, "hasFile": False}); releases._get_whisparr_client = lambda: w
out = releases.dismiss("stashdb", "s1")
check("wishlist scene, monitored in Whisparr -> unmonitored", w.calls == [(7, False)] and out.get("whisparr_unmonitored"))
w = Whisparr({"id": 8, "monitored": False, "hasFile": False}); releases._get_whisparr_client = lambda: w
releases.dismiss("stashdb", "s1")
check("already unmonitored -> nothing to do", w.calls == [])
w = Whisparr({"id": 9, "monitored": True, "hasFile": True}); releases._get_whisparr_client = lambda: w
releases.dismiss("stashdb", "s1")
check("movie with a file is never touched", w.calls == [])
w = Whisparr({"id": 10, "monitored": True, "hasFile": False}); releases._get_whisparr_client = lambda: w
releases.dismiss("stashdb", "s2")
check("a 'new' release (not on the wishlist) does not call Whisparr", w.calls == [])
class Boom:
    def find_by_stash_id(self, sid): raise RuntimeError("down")
releases._get_whisparr_client = lambda: Boom()
with db.connect() as c: c.execute("UPDATE release SET status='sent_to_whisparr' WHERE scene_stashdb_id='s1'")
out = releases.dismiss("stashdb", "s1")
check("Whisparr down -> the dismiss still succeeds", out.get("ok"))
print("\nALL OK" if all(res) else "\nFAILED"); sys.exit(0 if all(res) else 1)
