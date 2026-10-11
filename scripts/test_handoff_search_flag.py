"""Add / Add + search: search=False only puts the release on the wishlist; a slow search does not block the click (v0.205.0)."""
import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import handoff

fails = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

# fast search: result comes back directly
r = handoff.search_with_wait(("p", "1"), lambda: {"grab": None, "result": "no_results"})
check("fast search returns its result", r == {"grab": None, "result": "no_results"})

# slow search: returns running at once, keeps going, and a second call does not start another one
calls = []
gate = threading.Event()
def slow():
    calls.append(1); gate.wait(5); return {"grab": None, "result": "done"}
t0 = time.time()
r1 = handoff.search_with_wait(("p", "2"), slow, wait=0.3)
check("slow search -> running after the wait", r1 == {"running": True} and time.time() - t0 < 2)
r2 = handoff.search_with_wait(("p", "2"), slow, wait=0.3)
check("second click while it runs -> no second search", r2.get("running") and r2.get("again") and len(calls) == 1)
gate.set(); time.sleep(0.5)
check("finished search is forgotten, a new one may start", ("p", "2") not in handoff._running)
r3 = handoff.search_with_wait(("p", "2"), lambda: {"grab": None, "result": "again"})
check("new search after it finished", r3["result"] == "again")

# search=False: status flip only, no search call
import release_repo
class Rel(dict): pass
flipped = []
handoff.resolve_whisparr_scope_preset = lambda db: "no_whisparr"
release_repo.get_release = lambda db, p, s, et, eid: {"status": "new", "title": "t", "scene_date": "2020-01-01", "is_vr": 0, "studio_name": "S"}
release_repo.set_status_for_scene = lambda db, p, s, st: flipped.append(st)
def boom(*a, **k): raise AssertionError("search must not run")
handoff.attempt_sidecar_search = boom
out = handoff.send_to_sidecar(None, "theporndb", "x", "studio", "y", None, False, None, None, search=False)
check("Add: on the wishlist, no search", flipped == ["sent_to_whisparr"] and out.get("search_skipped") and not out["grabbed"])
print("\nALL OK" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
