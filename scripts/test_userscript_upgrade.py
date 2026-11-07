"""Tests (v0.251.0): the userscript sends a scene Stash already has as an upgrade (`replace`), a scene Stash does not have through the normal hand-off.
Run:  venv/bin/python scripts/test_userscript_upgrade.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from fastapi import HTTPException  # noqa: E402

from routers import search as search_router  # noqa: E402
from routers import userscript as us  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))
us.init_userscript_router(db, None, None, lambda: "http://stash.example:9999/")
search_router._db = db

class FakeClient:
    def get_scene_full(self, sid):
        return {"id": sid, "title": "T"}
search_router._client_for = lambda provider: FakeClient()
search_router._ids_in_library = lambda provider, ids: {"have": "77"} if "have" in ids else {}
calls = {"readd": [], "handoff": []}
reply = {"queued": True, "candidate_id": 5}
def fake_readd(local, reason, provider_duration, search):
    calls["readd"].append((local, reason, search))
    return dict(reply)
search_router._readd = fake_readd
search_router.handoff_scene = lambda provider, scene, search: calls["handoff"].append((scene["id"], search)) or {"already_sent": False}

body = {"origin": "https://stashdb.org", "id": "have", "replace": True}
out = us.wishlist(body, True)
check("a scene Stash has goes on the upgrade list", calls["readd"] == [("77", "readd", True)] and calls["handoff"] == [])
check("the answer says it replaces", out.get("replace") is True and out.get("queued") is True)

calls["readd"].clear()
us.wishlist(body, False)
check("without search it is only queued", calls["readd"] == [("77", "readd", False)])

reply.clear(); reply.update({"error": "a download for this file is already running"})
try:
    us.wishlist(body, True)
    check("a running download is refused", False)
except HTTPException as e:
    check("a running download is refused", e.status_code == 409 and "already running" in e.detail)

calls["readd"].clear()
us.wishlist({"origin": "https://stashdb.org", "id": "new", "replace": True}, True)
check("replace for a scene Stash does not have: the normal hand-off", calls["readd"] == [] and calls["handoff"] == [("new", True)])
calls["handoff"].clear()
us.wishlist({"origin": "https://stashdb.org", "id": "have"}, True)
check("without the flag a scene Stash has takes the normal hand-off (which reports it)", calls["readd"] == [] and calls["handoff"] == [("have", True)])
print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
