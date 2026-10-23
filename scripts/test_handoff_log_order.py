"""hand_off() calls on_start(already_sent) before the search runs, so "Added to the wishlist" is logged before the search's own entries (v0.230.1)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import handoff
import release_repo

fails = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

order = []
handoff.resolve_whisparr_scope_preset = lambda db: "no_whisparr"
release_repo.get_release = lambda db, p, s, et, eid: {"status": "new", "title": "t", "scene_date": "2020-01-01", "is_vr": 0, "studio_name": "S"}
release_repo.set_status_for_scene = lambda *a: None
handoff.link_imported_equivalent = lambda *a: None
def fake_search(*a, **k):
    order.append("search"); return {"result": "x", "grab": None}
handoff.attempt_sidecar_search = fake_search

out = handoff.hand_off(None, None, "theporndb", "s1", "studio", "e1", 1, 1, "/", None, False,
                       get_stash_client=lambda: None, on_start=lambda already: order.append(("start", already)))
check("on_start comes before the search, with already_sent False", order == [("start", False), "search"])

order.clear()
release_repo.get_release = lambda *a: {"status": "sent_to_whisparr", "title": "t", "scene_date": "2020-01-01", "is_vr": 0, "studio_name": "S"}
handoff.hand_off(None, None, "theporndb", "s1", "studio", "e1", 1, 1, "/", None, False, get_stash_client=lambda: None,
                 on_start=lambda already: order.append(("start", already)))
check("already on the wishlist -> already_sent True", order[0] == ("start", True))

order.clear()
handoff.link_imported_equivalent = lambda *a: {"provider": "stashdb", "scene_stashdb_id": "o", "title": "t"}
out = handoff.hand_off(None, None, "theporndb", "s1", "studio", "e1", 1, 1, "/", None, False, get_stash_client=lambda: None,
                       on_start=lambda already: order.append("start"))
check("already in the library -> no on_start, no search", out.get("already_in_library") and order == [])

handoff.link_imported_equivalent = lambda *a: None
release_repo.get_release = lambda *a: None
try:
    handoff.hand_off(None, None, "theporndb", "s1", "studio", "e1", 1, 1, "/", None, False, on_start=lambda a: order.append("start"))
    raised = False
except ValueError:
    raised = True
check("unknown release -> error, nothing logged", raised and order == [])

handoff.resolve_whisparr_scope_preset = lambda db: "full_whisparr"
release_repo.get_release = lambda *a: {"status": "new"}
try:
    handoff.hand_off(None, None, "theporndb", "s1", "studio", "e1", 1, 1, "/", None, False, on_start=lambda a: order.append("start"))
    raised = False
except ValueError:
    raised = True
check("full_whisparr + other provider -> error, nothing logged", raised and order == [])
print("\nALL OK" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
