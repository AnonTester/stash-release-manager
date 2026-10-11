"""Tests (v0.174.0): the release identity used for the blocklist / rejected titles, and the shared refresh of a scene's title + cast from the
provider. Pure functions and fakes, no network, no real database. Run:  venv/bin/python scripts/test_search_identity.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import handoff  # noqa: E402
import indexer_search as ix  # noqa: E402
import release_repo  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)


same = lambda a, b: ix._review_identity(a) == ix._review_identity(b)  # noqa: E731
check("the real case: Anna 1080p and Anna 1080P.x265 are the same release", same("Anna.Placeholder.Title.Here.1080p", "Anna.Placeholder.Title.Here.1080P.x265"))
check("the real case: Beth 1080p and Beth 1080P.x265", same("Beth...Placeholder.Title.Here.1080p", "Beth...Placeholder.Title.Here.1080P.x265"))
check("another resolution of the same release", same("Studio.Scene.Name.2160p.MP4-GRP", "Studio.Scene.Name.720p.MP4-GRP"))
check("codec only (h265 vs x264)", same("Some.Scene.h265", "Some.Scene.x264"))
check("the XXX marker still works", same("Show.Title.XXX.1080p.MP4-GRP", "Show.Title.XXX.2160p.MP4-GRP"))
check("a DIFFERENT performer is a different release", not same("Anna.Placeholder.Title.Here.1080p", "Alice.Placeholder.Title.Here.1080p"))
check("a different date is a different release", not same("ExampleStudio.Carla.24.07.16.1080p", "ExampleStudio.Carla.24.07.23.1080p"))
check("no marker at all: the whole name counts", not same("Just A Name", "Just A Name Two"))
check("None is safe", ix._review_identity(None) == "")

DB = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
with DB.connect() as c:
    release_repo.insert_release(DB, c, {"id": "s1", "title": "placeholder title", "created": "2026-09-15", "date": "2026-09-15"}, "stashdb", "studio", "st1", status="sent_to_whisparr")
class Client:
    def __init__(self, detail): self.detail = detail
    def get_scene_detail(self, sid):
        if isinstance(self.detail, Exception): raise self.detail
        return self.detail
DETAIL = {"title": "Example Renamed Title", "date": "2026-09-15", "performers": [{"performer": {"name": "Performer One"}}, {"performer": {"name": "Performer Two"}}]}
t, d, p, ok = handoff.refresh_scene_from_provider(DB, "stashdb", "s1", "placeholder title", "2026-09-15", lambda prov: Client(DETAIL), write=False)
check("dry run: gives the new title and the cast", t == "Example Renamed Title" and p == ["Performer One", "Performer Two"])
check("dry run: stores nothing", release_repo.list_rows_for_scene(DB, "stashdb", "s1")[0]["title"] == "placeholder title")
import history  # noqa: E402
history.init(DB)
t, d, p, ok = handoff.refresh_scene_from_provider(DB, "stashdb", "s1", "placeholder title", "2026-09-15", lambda prov: Client(DETAIL))
check("lookup works: ok=True", ok is True)
check("write: the renamed title is stored", release_repo.list_rows_for_scene(DB, "stashdb", "s1")[0]["title"] == "Example Renamed Title")
t, d, p, ok = handoff.refresh_scene_from_provider(DB, "stashdb", "s1", "kept title", "2026-09-15", lambda prov: Client(RuntimeError("down")))
check("lookup FAILS: stored values, no cast, ok=False (the caller must not search)", t == "kept title" and p == [] and ok is False)
t, d, p, ok = handoff.refresh_scene_from_provider(DB, "stashdb", "s1", "kept title", "2026-09-15", lambda prov: Client(None))
check("provider does not know the scene: stored values, no cast, ok=True (searched as before)", t == "kept title" and p == [] and ok is True)
class Feed:
    name = "feed"
    def search(self, q): return []
rel = release_repo.list_rows_for_scene(DB, "stashdb", "s1")[0]
check("no lead in the feed: no lookup is wanted", handoff.feed_has_lead(DB, "stashdb", "s1", rel, Feed()) is False)
check("any other search source: always true", handoff.feed_has_lead(DB, "stashdb", "s1", rel, object()) is True)

# the immediate hand-off to Whisparr (_attempt_search) searches the CURRENT title / cast too
CALL = {}
def fake_search(db, wc, mid, provider, sid, title, date, performers, *a, **k):
    CALL.update(title=title, performers=performers); return {"result": "stub", "grab": None}
handoff.search_and_grab = fake_search
rel2 = dict(release_repo.list_rows_for_scene(DB, "stashdb", "s1")[0]); rel2["title"] = "placeholder title"
handoff._attempt_search(DB, None, {"id": 1, "performerNames": ["Whisparr Cast"]}, "stashdb", "s1", rel2, None, False, None, lambda prov: Client(DETAIL))
check("hand-off to Whisparr: the current title and the provider's cast are searched", CALL["title"] == "Example Renamed Title" and CALL["performers"] == ["Performer One", "Performer Two"])
handoff._attempt_search(DB, None, {"id": 1, "performerNames": ["Whisparr Cast"]}, "stashdb", "s1", rel2, None, False, None, None)
check("hand-off to Whisparr without a provider client: stored title, Whisparr's own cast (as before)", CALL["title"] == "placeholder title" and CALL["performers"] == ["Whisparr Cast"])
# a failed lookup means NO search (sidecar path and Whisparr hand-off), the reason is recorded
SEARCHED = []
handoff.search_and_grab = lambda *a, **k: SEARCHED.append(1) or {"result": "x", "grab": None}
down = lambda prov: Client(RuntimeError("timeout"))  # noqa: E731
relx = dict(release_repo.list_rows_for_scene(DB, "stashdb", "s1")[0])
out = handoff.attempt_sidecar_search(DB, "stashdb", "s1", relx, None, False, object(), down)
check("sidecar path: lookup failed -> not searched, result provider_lookup_failed", out["result"] == "provider_lookup_failed" and not SEARCHED
      and release_repo.list_rows_for_scene(DB, "stashdb", "s1")[0]["last_search_result"] == "provider_lookup_failed")
out = handoff._attempt_search(DB, None, {"id": 1, "performerNames": ["X"]}, "stashdb", "s1", relx, None, False, None, down)
check("Whisparr hand-off: lookup failed -> not searched", out["result"] == "provider_lookup_failed" and not SEARCHED)
out = handoff.attempt_sidecar_search(DB, "stashdb", "s1", relx, None, False, object(), lambda prov: Client(DETAIL))
check("lookup works: searched", SEARCHED == [1])
out = handoff._attempt_search(DB, None, {"id": 1}, "stashdb", "s1", relx, None, False, None, None)
check("no provider client at all (fix-mismatch re-add): searched with the stored values", SEARCHED == [1, 1])
print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
