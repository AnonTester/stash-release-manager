"""Test (v0.186.0): taking over the Stash API key and stash-boxes from Stash (api/stashbox_sync.py, routers/stash_boxes.py) and the
plugin script's discovery (plugin/stash_release_manager_backend.py), against a throw-away database. Run:  python3 scripts/test_stashbox_sync.py"""
import json
import os
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "api"))
sys.path.insert(0, os.path.join(ROOT, "plugin"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
for k in ("STASH_URL", "STASH_API_KEY", "STASHDB_API_KEY", "THEPORNDB_API_KEY"):
    os.environ.pop(k, None)

import metadata_provider_repo  # noqa: E402
import stashbox_sync  # noqa: E402
from db import Database  # noqa: E402
from routers import settings as settings_router  # noqa: E402
from routers import stash_boxes  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)


def fresh():
    d = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
    settings_router._db = d
    stash_boxes.init_stash_boxes_router(d)
    return d
def put(d, k, v):  # noqa: E302
    with d.connect() as c: c.execute("INSERT OR REPLACE INTO setting (key, value) VALUES (?, ?)", (k, v))
def get(d, k):  # noqa: E302
    with d.connect() as c:
        row = c.execute("SELECT value FROM setting WHERE key = ?", (k,)).fetchone()
    return row["value"] if row else ""
def box(name, endpoint, key="k"):  # noqa: E302
    return {"name": name, "endpoint": endpoint, "api_key": key}
def by_endpoint(report, endpoint):  # noqa: E302
    return next(b for b in report["boxes"] if b["endpoint"] == endpoint)


STASHDB = "https://stashdb.org/graphql"
TPDB = "https://theporndb.net/graphql"
JAV = "https://javstash.org/graphql"
FANS = "https://fansdb.cc/graphql"

# -- classification / keys
check("classify: stashdb.org", stashbox_sync.classify(STASHDB) == "stashdb")
check("classify: theporndb.net", stashbox_sync.classify(TPDB) == "theporndb")
check("classify: a look-alike host is not StashDB", stashbox_sync.classify("https://stashdb.org.evil.example/graphql") is None)
check("classify: other stash-box", stashbox_sync.classify(JAV) is None)
check("normalize: case and trailing slash", stashbox_sync.normalize_endpoint(" HTTPS://FansDB.cc/graphql/ ") == FANS)
check("slug: name becomes a valid key", stashbox_sync.slug_key("Fans DB!", FANS, set()) == "fans_db")
check("slug: reserved key is numbered", stashbox_sync.slug_key("StashDB", STASHDB, set()) != "stashdb")
check("slug: taken key is numbered", stashbox_sync.slug_key("fansdb", FANS, {"fansdb"}) == "fansdb_2")
check("slug: starts with a letter", stashbox_sync.slug_key("3rd Party", "https://x.example/graphql", set())[0].isalpha())
check("slug: result always passes the repo's validation", all(
    metadata_provider_repo._KEY_RE.match(stashbox_sync.slug_key(n, "https://h.example/graphql", set()))
    for n in ("", "!!", "A", "x" * 80, "stash", "Ünï")))

# -- fresh installation
d = fresh()
r = stashbox_sync.apply_sync(d, "stash-secret", [box("StashDB", STASHDB, "sdb"), box("ThePornDB", TPDB, "tpdb"), box("JAVStash", JAV, "jav")],
                             stash_url_suggested="http://stash.example:9999/")
check("fresh: Stash API key taken over", get(d, "stash_api_key") == "stash-secret" and r["stash_api_key"] == "updated")
check("fresh: Stash URL taken from the suggestion (no trailing slash)", get(d, "stash_url") == "http://stash.example:9999" and r["stash_url"] == "set")
check("fresh: StashDB key and endpoint filled", get(d, "stashdb_api_key") == "sdb" and get(d, "stashdb_url") == STASHDB)
check("fresh: ThePornDB key filled, its REST url left alone", get(d, "theporndb_api_key") == "tpdb" and get(d, "theporndb_url") == "")
check("fresh: another stash-box is only offered, no row", by_endpoint(r, JAV)["status"] == "available" and metadata_provider_repo.list_all(d) == [])
check("fresh: the report carries no key", "sdb" not in json.dumps(r) and "stash-secret" not in json.dumps(r) and "jav" not in json.dumps(r).replace("javstash", ""))

# -- add on request
r = stashbox_sync.apply_sync(d, "stash-secret", [box("JAVStash", JAV, "jav"), box("FansDB", FANS, "fans")], add=[JAV])
rows = {p["key"]: p for p in metadata_provider_repo.list_all(d)}
check("add: only the named endpoint becomes a provider", list(rows) == ["javstash"] and rows["javstash"]["url"] == JAV and rows["javstash"]["api_key"] == "jav")
check("add: the other one stays offered", by_endpoint(r, FANS)["status"] == "available")
check("add: report names the new provider key", by_endpoint(r, JAV)["status"] == "added" and by_endpoint(r, JAV)["provider_key"] == "javstash")
check("add: the new provider gets a stored colour", (metadata_provider_repo.get_one(d, "javstash") or {}).get("color", "").startswith("#"))
r = stashbox_sync.apply_sync(d, "stash-secret", [box("JAVStash", JAV + "/", "jav")], add=[JAV])
check("add: a second run (URL with trailing slash) does not duplicate", len(metadata_provider_repo.list_all(d)) == 1 and by_endpoint(r, JAV + "/")["status"] == "present")
r = stashbox_sync.apply_sync(d, "", [box("NoKey", "https://nokey.example/graphql", "")], add=["https://nokey.example/graphql"])
check("add: a stash-box without a key in Stash is skipped, not added", by_endpoint(r, "https://nokey.example/graphql")["status"] == "skipped" and len(metadata_provider_repo.list_all(d)) == 1)

# -- typed values win, overwrite replaces
d = fresh()
put(d, "stashdb_api_key", "typed"); put(d, "stash_url", "http://stash:9999")
metadata_provider_repo.create(d, "javstash", "JAV", JAV, "typed-jav", True)
r = stashbox_sync.apply_sync(d, "", [box("StashDB", STASHDB, "from-stash"), box("JAV", JAV, "from-stash-jav")], stash_url_suggested="http://other:1")
check("typed: StashDB key kept", get(d, "stashdb_api_key") == "typed" and by_endpoint(r, STASHDB)["status"] == "present")
check("typed: provider key kept", metadata_provider_repo.get_one(d, "javstash")["api_key"] == "typed-jav")
check("typed: existing Stash URL is never replaced by the suggestion", get(d, "stash_url") == "http://stash:9999" and r["stash_url"] == "present")
check("typed: an empty Stash answer leaves the Stash API key alone", r["stash_api_key"] == "none" and get(d, "stash_api_key") == "")
r = stashbox_sync.apply_sync(d, "", [box("StashDB", STASHDB, "from-stash"), box("JAV", JAV + "/", "from-stash-jav")], overwrite=True)
check("overwrite: StashDB key replaced", get(d, "stashdb_api_key") == "from-stash" and by_endpoint(r, STASHDB)["status"] == "updated")
row = metadata_provider_repo.get_one(d, "javstash")
check("overwrite: provider key replaced and URL taken verbatim from Stash", row["api_key"] == "from-stash-jav" and row["url"] == JAV + "/")

# -- the Stash key follows Stash (rotation); .env does not block an empty DB
d = fresh()
put(d, "stash_api_key", "old")
r = stashbox_sync.apply_sync(d, "new", [])
check("rotation: a regenerated Stash key replaces the stored one", get(d, "stash_api_key") == "new" and r["stash_api_key"] == "updated")
r = stashbox_sync.apply_sync(d, "new", [])
check("rotation: same key again reports unchanged", r["stash_api_key"] == "unchanged")

# -- router
d = fresh()
out = stash_boxes.sync(stash_boxes.SyncBody(stash_api_key="k1", boxes=[stash_boxes.StashBox(name="StashDB", endpoint=STASHDB, api_key="s")],
                                            stash_url_suggested="http://s:9999"))
check("router: sync round trip", get(d, "stashdb_api_key") == "s" and get(d, "stash_api_key") == "k1" and out["boxes"][0]["kind"] == "stashdb")

# -- the plugin script's discovery (Stash GraphQL faked)
import stash_release_manager_backend as backend  # noqa: E402


class FakeResp:
    def __init__(self, body): self._body = body
    def raise_for_status(self): pass
    def json(self): return self._body


def with_post(handler, fn):
    real = backend.requests.post
    backend.requests.post = handler
    try:
        return fn()
    finally:
        backend.requests.post = real


conn = {"server_connection": {"Scheme": "http", "Port": 9999, "SessionCookie": {"Name": "session", "Value": "abc"}}}
seen = {}
def ok_post(url, json=None, cookies=None, timeout=None, verify=None):  # noqa: E302
    seen.update(url=url, cookies=cookies, query=json["query"])
    return FakeResp({"data": {"configuration": {"general": {"apiKey": "K", "stashBoxes": [
        {"name": "StashDB", "endpoint": STASHDB, "api_key": "a"}, {"name": None, "endpoint": JAV, "api_key": None}]}}}})
found = with_post(ok_post, lambda: backend._discover_from_stash(conn))
check("discover: key and boxes read, null fields become ''", found == {"api_key": "K", "boxes": [
    {"name": "StashDB", "endpoint": STASHDB, "api_key": "a"}, {"name": "", "endpoint": JAV, "api_key": ""}]})
check("discover: loopback call with the session cookie, no API key", seen["url"] == "http://localhost:9999/graphql" and seen["cookies"] == {"session": "abc"})
check("discover: no server_connection -> None (no request made)", with_post(lambda *a, **k: 1 / 0, lambda: backend._discover_from_stash({})) is None)
found = with_post(lambda *a, **k: FakeResp({"data": {"configuration": {"general": {"apiKey": "", "stashBoxes": []}}}}), lambda: backend._discover_from_stash(conn))
check("discover: Stash without login / boxes -> empty key, no boxes", found == {"api_key": "", "boxes": []})
check("discover: GraphQL errors -> None", with_post(lambda *a, **k: FakeResp({"errors": [{"message": "x"}]}), lambda: backend._discover_from_stash(conn)) is None)
def boom(*a, **k): raise OSError("down")  # noqa: E302
check("discover: connection error -> None", with_post(boom, lambda: backend._discover_from_stash(conn)) is None)

# _sync_from_stash posts to the sidecar and marks whether Stash could be read
posted = {}
def fake_sidecar_post(sidecar_url, path, data, timeout=30):  # noqa: E302
    posted.update(url=sidecar_url, path=path, data=data)
    return {"stash_api_key": "updated", "boxes": []}
real_post = backend._post
backend._post = fake_sidecar_post
try:
    rep = with_post(ok_post, lambda: backend._sync_from_stash("http://sidecar:6961", conn, {"stash_url_suggested": "http://x:1", "add": [JAV]}))
    check("sync: body carries key, boxes, suggestion and add list",
          posted["path"] == "/stash-boxes/sync" and posted["data"]["stash_api_key"] == "K" and posted["data"]["add"] == [JAV]
          and posted["data"]["stash_url_suggested"] == "http://x:1" and len(posted["data"]["boxes"]) == 2)
    check("sync: report says Stash was read and has no key in it", rep["discovered"] is True and "K" not in json.dumps(rep))
    rep = backend._sync_from_stash("http://sidecar:6961", {}, {})
    check("sync: Stash not readable -> still posts (URL suggestion), discovered False", rep["discovered"] is False and posted["data"]["boxes"] == [])
finally:
    backend._post = real_post

print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
