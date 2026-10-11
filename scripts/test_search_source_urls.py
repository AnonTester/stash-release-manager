"""Search page: source_url per hit, and the tag search of the stash-box client (v0.207.0)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from fastapi import HTTPException
from routers import search
import stashdb_client

class Box:  # stands in for a stash-box client
    url = "https://stash.example/graphql"

res = []
def check(name, cond):
    res.append(cond); print(("ok   " if cond else "FAIL ") + name)

r = search._add_source_urls("stashdb", Box(), "scene", [{"id": "s1"}])
check("stash-box scene -> site/scenes/id", r[0]["source_url"] == "https://stash.example/scenes/s1")
r = search._add_source_urls("fansdb", Box(), "studio", [{"id": "x"}])
check("stash-box studio -> site/studios/id", r[0]["source_url"] == "https://stash.example/studios/x")
r = search._add_source_urls("theporndb", None, "scene", [{"id": "uuid", "slug": "some-slug"}])
check("theporndb scene by slug", r[0]["source_url"] == "https://theporndb.net/scenes/some-slug")
r = search._add_source_urls("theporndb", None, "studio", [{"id": "uuid-1"}])
check("theporndb studio = sites/uuid", r[0]["source_url"] == "https://theporndb.net/sites/uuid-1")
r = search._add_source_urls("x", object(), "scene", [{"id": "1"}])
check("no site address -> no link", "source_url" not in r[0])

norm = stashdb_client.StashDBClient._normalize_search_scenes([{"id": "1", "images": [{"url": "u"}], "tags": [{"name": "VR"}], "performers": [{"performer": {"id": "p", "name": "N"}}]}])
check("scene normalization (image, performers flattened)", norm[0]["image_url"] == "u" and norm[0]["performers"] == [{"id": "p", "name": "N"}])

class Tpdb: pass
search._client_for = lambda provider: Tpdb()
try:
    search.search_tags("theporndb", "x"); check("theporndb tag search refused", False)
except HTTPException as e:
    check("theporndb tag search refused with a clear message", e.status_code == 400)
print("\nALL OK" if all(res) else "\nFAILED")
sys.exit(0 if all(res) else 1)
