"""Manual search marks only the release's OWN name as tried (not its other resolutions), with the real reason (v0.221.10).
Run: venv/bin/python scripts/test_manual_search_tried.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from db import Database
import release_repo
from routers import releases as rel

res = []
def check(name, cond):
    res.append(bool(cond)); print(("ok   " if cond else "FAIL ") + name)

class FakeSource:
    def search(self, q):
        return [{"title": t, "indexerId": 1, "age": 1, "size": 1} for t in (
            "Site.26.09.12.Anna.XXX.2160p.MP4-GRP", "Site.26.09.12.Anna.XXX.MP4-GRPSD", "Site.26.09.12.Anna.XXX.1080p.MP4-GRP")]
    def indexer_priorities(self): return {}

d = tempfile.mkdtemp()
db = Database(os.path.join(d, "t.sqlite3"))
with db.connect() as c:
    c.execute("INSERT INTO release (provider, scene_stashdb_id, entity_type, entity_stashdb_id, title, status, scene_created) VALUES ('stashdb','s1','studio','e1','T','new','2026-01-01')")
    c.execute("INSERT INTO history (level, kind, category, message, provider, scene_id) VALUES ('info','history','review','Possible match rejected (blocklisted): Site.26.09.12.Anna.XXX.MP4-GRPSD','stashdb','s1')")
    c.execute("INSERT INTO history (level, kind, category, message, provider, scene_id) VALUES ('warning','history','download','Download failed in SABnzbd: Site.26.09.12.Anna.XXX.2160p.MP4-GRP -- Duplicate NZB','stashdb','s1')")
release_repo.add_review_rejected_title(db, "stashdb", "s1", "Site.26.09.12.Anna.XXX.2160p.MP4-GRP")
release_repo.add_review_rejected_title(db, "stashdb", "s1", "Site.26.09.12.Anna.XXX.MP4-GRPSD")
rel._db = db
rel._get_manual_search_sources = lambda: {"x": FakeSource()}
out = {r["title"]: r.get("tried") for r in rel.manual_search("stashdb", "s1", {"query": "q"})["results"]}
check("the failed 2160p is marked as a failed download", out["Site.26.09.12.Anna.XXX.2160p.MP4-GRP"].startswith("download failed"))
check("the SD one is marked as declined in Needs review", out["Site.26.09.12.Anna.XXX.MP4-GRPSD"].startswith("declined in Needs review"))
check("the 1080p sibling of the failed 2160p is NOT marked", out["Site.26.09.12.Anna.XXX.1080p.MP4-GRP"] is None)
# a movie's manual search (provider theporndb, "film-<id>"): the movie's own rejected names are marked, its other resolutions are not
import film_repo
fid, _ = film_repo.add(db, title="Example Film 2", year=2000, tpdb_id="tp1", status="wanted")
film_repo.update(db, fid, rejected_titles=["Site.26.09.12.Anna.XXX.2160p.MP4-GRP"])
for sid in ("tp1", f"film-{fid}"):
    out = {r["title"]: r.get("tried") for r in rel.manual_search("theporndb", sid, {"query": "q"})["results"]}
    check(f"movie ({sid}): its rejected release is marked", bool(out["Site.26.09.12.Anna.XXX.2160p.MP4-GRP"]) and "movie" in out["Site.26.09.12.Anna.XXX.2160p.MP4-GRP"])
    check(f"movie ({sid}): the same release in another resolution is not", out["Site.26.09.12.Anna.XXX.1080p.MP4-GRP"] is None)
sys.exit(0 if all(res) else 1)
