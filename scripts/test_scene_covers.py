"""Scene covers (v0.254.0), performer pictures and studio logos (v0.256.0): in Stash -> Stash's picture and no cached copy; otherwise the
stash-box picture, fetched once, scaled and cached."""
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
def thumb(data, kind="scene"): return (b"PNG" if kind == "studio" else b"JPEG") + data

clock = [1000.0]
c = sc.SceneCovers(db, tmp, lambda: Stash(), lambda p: prov, fetch=fetch, thumb=thumb, now=lambda: clock[0])

# a stale copy of the scene that is in Stash is deleted
c._write(c.path("scene", "stashdb", "D"), b"old")
out = c.covers_for_scenes([("stashdb", "A"), ("stashdb", "B"), ("stashdb", "C"), ("stashdb", "D"), ("theporndb", "T"), ("stashdb", "A")])
check("in Stash: Stash's cover", out["stashdb:D"] == {"stash": "77"})
check("in Stash: the cached copy is gone", not os.path.exists(c.path("scene", "stashdb", "D")))
check("stored URL used, cached as a data: URL", out["stashdb:A"]["data"].startswith("data:image/jpeg;base64,") and "https://cdn.example/a.jpg" in fetched)
check("no stored URL: the provider is asked", "B" in prov.asked and "https://cdn.example/B.jpg" in fetched)
check("ThePornDB: the mirrored image from the scene detail", "https://tpdb.example/T.jpg" in fetched)
check("provider without an image: null", out["stashdb:C"] is None)
check("the files are on disk", all(os.path.isfile(c.path("scene", "stashdb", x)) for x in "AB") and os.path.isfile(c.path("scene", "theporndb", "T")))
check("the same scene twice in one request is fetched once", fetched.count("https://cdn.example/a.jpg") == 1)

n = len(fetched); asked = len(prov.asked)
out = c.covers_for_scenes([("stashdb", "A"), ("stashdb", "B"), ("stashdb", "C")])
check("second view: from the cache, nothing fetched or asked", len(fetched) == n and len(prov.asked) == asked and out["stashdb:A"]["data"])
check("second view: the `no image` answer is remembered for a day", out["stashdb:C"] is None and len(prov.asked) == asked)
clock[0] += sc.NONE_RETRY_S + 1
c.covers_for_scenes([("stashdb", "C")])
check("a day later the provider is asked again", len(prov.asked) == asked + 1)

# many scenes: the page asks again for the rest
for i in range(sc.MAX_FETCHES + 5):
    add("stashdb", f"M{i}", f"https://cdn.example/m{i}.jpg")
out = c.covers_for_scenes([("stashdb", f"M{i}") for i in range(sc.MAX_FETCHES + 5)])
pend = [k for k, v in out.items() if v and v.get("pending")]
check("more than a batch: the rest is `pending`", len(pend) == 5 and sum(1 for v in out.values() if v and v.get("data")) == sc.MAX_FETCHES)
out = c.covers_for_scenes([("stashdb", f"M{i}") for i in range(sc.MAX_FETCHES + 5)])
check("asked again: all there", not any(v is None or v.get("pending") for v in out.values()))

# Stash cannot be asked: the stash-box cover is used and nothing is purged
class DeadStash:
    def local_scene_ids_by_stash_ids(self, pairs): raise OSError("down")
c2 = sc.SceneCovers(db, tmp, lambda: DeadStash(), lambda p: prov, fetch=fetch, thumb=thumb, now=lambda: clock[0])
out = c2.covers_for_scenes([("stashdb", "A")])
check("Stash down: stash-box cover", out["stashdb:A"]["data"])

# the sweep drops covers of scenes without a release row
c._write(c.path("scene", "stashdb", "GONE"), b"x")
c._write(c.path("scene", "stashdb", "A") + ".1.tmp", b"x")
removed = c.sweep()
check("sweep: only the orphans (and leftovers of an interrupted write) go", removed == 2 and os.path.isfile(c.path("scene", "stashdb", "A")) and not os.path.exists(c.path("scene", "stashdb", "GONE")))

# a failed download is remembered, not retried on every view
def boom(url): raise OSError("403")
add("stashdb", "F", "https://cdn.example/f.jpg")
c3 = sc.SceneCovers(db, tmp, lambda: Stash(), lambda p: prov, fetch=boom, thumb=thumb, now=lambda: clock[0])
check("blocked image: null", c3.covers_for_scenes([("stashdb", "F")])["stashdb:F"] is None and c3._none_fresh("scene", "stashdb", "F"))


# -- performers and studios (v0.256.0)
class Stash2(Stash):
    def local_entity_ids_by_stash_ids(self, kind, pairs, endpoint_of):
        known = {("performer", "stashdb", "P_LOCAL"): "5", ("studio", "stashdb", "S_LOCAL"): "9"}
        return {(p, i): known[(kind, p, i)] for p, i in pairs if (kind, p, i) in known and endpoint_of(p)}

class Prov2(Prov):
    def fetch_performer_image(self, pid):
        self.asked.append(("performer", pid)); return f"https://cdn.example/perf-{pid}.jpg"
    def fetch_studio_image(self, sid):
        self.asked.append(("studio", sid)); return None if sid == "S_NONE" else f"https://cdn.example/studio-{sid}.png"

prov2 = Prov2(); fetched.clear()
c4 = sc.SceneCovers(db, tmp, lambda: Stash2(), lambda p: prov2, fetch=fetch, thumb=thumb, now=lambda: clock[0], endpoint_of=lambda p: "https://stashdb.org/graphql")
sc.remember_hints(db, "performer", "stashdb", [{"id": "P_HINT", "image_url": "https://hint.example/p.jpg"}, {"id": "P_BAD", "image_url": "javascript:x"}, {"id": "P_NO"}])
c4._write(c4.path("performer", "stashdb", "P_LOCAL"), b"old")
items = [("performer", "stashdb", "P_HINT"), ("performer", "stashdb", "P_ASK"), ("performer", "stashdb", "P_LOCAL"),
         ("studio", "stashdb", "S1"), ("studio", "stashdb", "S_LOCAL"), ("studio", "stashdb", "S_NONE"), ("performer", "stashdb", "P_BAD")]
out = c4.covers_for(items)
check("performer in Stash: Stash's picture, cached copy deleted", out["performer:stashdb:P_LOCAL"] == {"stash": "5"} and not os.path.exists(c4.path("performer", "stashdb", "P_LOCAL")))
check("studio in Stash: Stash's picture", out["studio:stashdb:S_LOCAL"] == {"stash": "9"})
check("the address the stash-box sent is used (no provider request)", "https://hint.example/p.jpg" in fetched and ("performer", "P_HINT") not in prov2.asked)
check("no hint: the stash-box is asked for that one performer", ("performer", "P_ASK") in prov2.asked and "https://cdn.example/perf-P_ASK.jpg" in fetched)
check("a non-http hint is not kept (the provider is asked instead)", ("performer", "P_BAD") in prov2.asked)
check("performers are JPEG", out["performer:stashdb:P_HINT"]["data"].startswith("data:image/jpeg;base64,") and c4.path("performer", "stashdb", "P_HINT").endswith(".jpg"))
check("studio logos are PNG (transparency)", out["studio:stashdb:S1"]["data"].startswith("data:image/png;base64,") and c4.path("studio", "stashdb", "S1").endswith(".png"))
check("studio without an image: null", out["studio:stashdb:S_NONE"] is None)
n = len(fetched)
out = c4.covers_for(items[:2] + items[3:4])
check("second view comes from the cache", len(fetched) == n and out["studio:stashdb:S1"]["data"] and out["performer:stashdb:P_HINT"]["data"])
check("the plain scene form still answers with provider:id keys", c4.covers_for_scenes([("stashdb", "A")])["stashdb:A"]["data"])

# a performer picture nobody used for a long time is swept, a recently used one stays; old hints go
clock[0] += (sc.UNUSED_DAYS + 1) * 86400
for kind, i in (("performer", "P_HINT"), ("studio", "S1")):
    os.utime(c4.path(kind, "stashdb", i), (clock[0] - 86400, clock[0] - 86400) if i == "S1" else (clock[0] - (sc.UNUSED_DAYS + 1) * 86400,) * 2)
with db.connect() as conn:
    conn.execute("UPDATE image_hint SET seen_at = datetime('now', '-40 days') WHERE entity_id = 'P_HINT'")
c4.sweep()
check("sweep: unused performer picture removed, recently used logo kept", not os.path.exists(c4.path("performer", "stashdb", "P_HINT")) and os.path.exists(c4.path("studio", "stashdb", "S1")))
check("sweep: old picture addresses are pruned", sc._hint(db, "performer", "stashdb", "P_HINT") is None)

# scene folders of 0.254.0 (covers/<provider>/) are moved to covers/scene/<provider>/
legacy = tempfile.mkdtemp()
os.makedirs(os.path.join(legacy, "covers", "stashdb"))
open(os.path.join(legacy, "covers", "stashdb", "OLD.jpg"), "wb").write(b"x")
c5 = sc.SceneCovers(db, legacy, lambda: Stash(), lambda p: prov, fetch=fetch, thumb=thumb)
check("0.254.0 scene covers move to covers/scene/", os.path.isfile(c5.path("scene", "stashdb", "OLD")))

# the Stash lookup itself: aliased queries, endpoint per provider, placeholder pictures do not count
import stash_client
calls = []
sc_obj = object.__new__(stash_client.StashClient)
def fake_graphql(query, variables):
    calls.append(query)
    return {"s0": {"performers": [{"id": "7", "image_path": "/performer/7/image?t=1"}]}, "s1": {"performers": [{"id": "8", "image_path": "/performer/8/image?t=1&default=true"}]}, "s2": {"performers": []}}
sc_obj._graphql = fake_graphql
got = sc_obj.local_entity_ids_by_stash_ids("performer", [("stashdb", "a"), ("stashdb", "b"), ("stashdb", "c"), ("unknown", "d")], lambda p: None if p == "unknown" else "https://stashdb.org/graphql")
check("Stash lookup: in Stash with a picture only", got == {("stashdb", "a"): "7"})
check("Stash lookup: one request for the chunk, by stash_id_endpoint", len(calls) == 1 and "findPerformers" in calls[0] and "stash_id_endpoint" in calls[0] and calls[0].count("stash_id:") == 3)

sys.exit(1 if fails else 0)
