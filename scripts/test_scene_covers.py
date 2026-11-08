"""Scene covers (v0.254.0): in Stash -> Stash's cover and no cached copy; otherwise the stash-box cover, fetched once and cached."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import db as dbmod
import release_repo
import scene_covers as sc

fails = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

tmp = tempfile.mkdtemp()
db = dbmod.Database(os.path.join(tmp, "t.sqlite3")) if hasattr(dbmod, "Database") else dbmod.DB(os.path.join(tmp, "t.sqlite3"))
db.init() if hasattr(db, "init") else None

def add(provider, sid, image_url=None, status="new"):
    with db.connect() as conn:
        conn.execute("INSERT INTO release (provider, scene_stashdb_id, entity_type, entity_stashdb_id, title, scene_created, status, image_url) "
                     "VALUES (?, ?, 'studio', 'e1', 't', '2026-01-01', ?, ?)", (provider, sid, status, image_url))

add("stashdb", "A", "https://cdn.example/a.jpg")
add("stashdb", "B")                      # no stored URL -> asked from the provider
add("stashdb", "C")                      # the provider has no image
add("stashdb", "D", "https://cdn.example/d.jpg")   # in Stash
add("theporndb", "T")

class Stash:
    def local_scene_ids_by_stash_ids(self, pairs):
        return {p: "77" for p in pairs if p == ("stashdb", "D")}

class Prov:
    def __init__(self): self.asked = []
    def fetch_scene_image(self, sid):
        self.asked.append(sid)
        return None if sid == "C" else f"https://cdn.example/{sid}.jpg"
    def get_scene_detail(self, sid):
        self.asked.append(sid)
        return {"image_url": f"https://tpdb.example/{sid}.jpg"}

prov = Prov()
fetched = []
def fetch(url):
    fetched.append(url)
    return b"RAW" + url.encode()
def thumb(data): return b"JPEG" + data

clock = [1000.0]
c = sc.SceneCovers(db, tmp, lambda: Stash(), lambda p: prov, fetch=fetch, thumb=thumb, now=lambda: clock[0])

# a stale copy of the scene that is in Stash is deleted
c._write(c.path("stashdb", "D"), b"old")
out = c.covers_for([("stashdb", "A"), ("stashdb", "B"), ("stashdb", "C"), ("stashdb", "D"), ("theporndb", "T"), ("stashdb", "A")])
check("in Stash: Stash's cover", out["stashdb:D"] == {"stash": "77"})
check("in Stash: the cached copy is gone", not os.path.exists(c.path("stashdb", "D")))
check("stored URL used, cached as a data: URL", out["stashdb:A"]["data"].startswith("data:image/jpeg;base64,") and "https://cdn.example/a.jpg" in fetched)
check("no stored URL: the provider is asked", "B" in prov.asked and "https://cdn.example/B.jpg" in fetched)
check("ThePornDB: the mirrored image from the scene detail", "https://tpdb.example/T.jpg" in fetched)
check("provider without an image: null", out["stashdb:C"] is None)
check("the files are on disk", all(os.path.isfile(c.path("stashdb", x)) for x in "AB") and os.path.isfile(c.path("theporndb", "T")))
check("the same scene twice in one request is fetched once", fetched.count("https://cdn.example/a.jpg") == 1)

n = len(fetched); asked = len(prov.asked)
out = c.covers_for([("stashdb", "A"), ("stashdb", "B"), ("stashdb", "C")])
check("second view: from the cache, nothing fetched or asked", len(fetched) == n and len(prov.asked) == asked and out["stashdb:A"]["data"])
check("second view: the `no image` answer is remembered for a day", out["stashdb:C"] is None and len(prov.asked) == asked)
clock[0] += sc.NONE_RETRY_S + 1
c.covers_for([("stashdb", "C")])
check("a day later the provider is asked again", len(prov.asked) == asked + 1)

# many scenes: the page asks again for the rest
for i in range(sc.MAX_FETCHES + 5):
    add("stashdb", f"M{i}", f"https://cdn.example/m{i}.jpg")
out = c.covers_for([("stashdb", f"M{i}") for i in range(sc.MAX_FETCHES + 5)])
pend = [k for k, v in out.items() if v and v.get("pending")]
check("more than a batch: the rest is `pending`", len(pend) == 5 and sum(1 for v in out.values() if v and v.get("data")) == sc.MAX_FETCHES)
out = c.covers_for([("stashdb", f"M{i}") for i in range(sc.MAX_FETCHES + 5)])
check("asked again: all there", not any(v is None or v.get("pending") for v in out.values()))

# Stash cannot be asked: the stash-box cover is used and nothing is purged
class DeadStash:
    def local_scene_ids_by_stash_ids(self, pairs): raise OSError("down")
c2 = sc.SceneCovers(db, tmp, lambda: DeadStash(), lambda p: prov, fetch=fetch, thumb=thumb, now=lambda: clock[0])
out = c2.covers_for([("stashdb", "A")])
check("Stash down: stash-box cover", out["stashdb:A"]["data"])

# the sweep drops covers of scenes without a release row
c._write(c.path("stashdb", "GONE"), b"x")
c._write(c.path("stashdb", "A") + ".1.tmp", b"x")
removed = c.sweep()
check("sweep: only the orphans (and leftovers of an interrupted write) go", removed == 2 and os.path.isfile(c.path("stashdb", "A")) and not os.path.exists(c.path("stashdb", "GONE")))

# a failed download is remembered, not retried on every view
def boom(url): raise OSError("403")
add("stashdb", "F", "https://cdn.example/f.jpg")
c3 = sc.SceneCovers(db, tmp, lambda: Stash(), lambda p: prov, fetch=boom, thumb=thumb, now=lambda: clock[0])
check("blocked image: null", c3.covers_for([("stashdb", "F")])["stashdb:F"] is None and c3._none_fresh("stashdb", "F"))

sys.exit(1 if fails else 0)
