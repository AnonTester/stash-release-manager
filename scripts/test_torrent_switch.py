"""search_and_grab as the torrent monitor uses it (v0.231.0): the dead torrent is excluded by hash, a running download does not stop the grab
(replace_job), the scene's length is asked from the provider, and a same-named torrent with another hash stays eligible."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import history
import indexer_search as ix
import release_repo

fails = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

class Db:
    def __init__(self):
        self.c = sqlite3.connect(":memory:"); self.c.row_factory = sqlite3.Row
        self.c.execute("CREATE TABLE setting (key TEXT PRIMARY KEY, value TEXT)")
    def connect(self):
        c = self.c
        class Ctx:
            def __enter__(s): return c
            def __exit__(s, *a): c.commit()
        return Ctx()

H = lambda n: f"{n:040x}"
NAME = "Joymii.13.09.08.Alexis.B.Wet.And.Clean.XXX.1080p.MP4-KTR"
def rel(h, seeders, name=NAME):
    return {"title": name, "size": 700_000_000, "age": 100, "indexerId": 1, "indexer": "x", "protocol": "torrent", "downloadUrl": f"http://x/{h[-4:]}",
            "infoHash": h, "seeders": seeders, "peers": 1, "quality": {"quality": {"resolution": 1080, "source": "web"}}}

class Source:
    name, available, reason = "prowlarr", True, ""
    def __init__(self, results): self.results = results
    def search(self, q): return list(self.results)
    def indexer_priorities(self): return {}

state = {"active": True, "blocked": [], "grabbed": [], "duration_calls": []}
release_repo.scene_has_active_download = lambda db, p, s: state["active"]
release_repo.get_review_rejected_titles = lambda db, p, s: list(state["blocked"])
release_repo.grabbed_titles_of_other_scenes = lambda db, p, s: []
release_repo.set_search_result_for_scene = lambda *a: None
release_repo.set_search_details_for_scene = lambda *a: None
release_repo.set_download_job_for_scene = lambda *a: None
release_repo.set_grabbed_for_scene = lambda *a: None
release_repo.set_review_candidate_for_scene = lambda *a: None
release_repo.get_review_candidate_for_scene = lambda *a: None
history.scene = lambda *a, **k: None
ix.parent_studio_names = lambda *a: []
ix.studio_site_names = lambda *a, **k: []
ix.fallback_resolutions = lambda *a, **k: []
def fake_send(db, candidate, warn):
    state["grabbed"].append(candidate.get("infoHash"))
    return {"result": ix.RESULT_GRABBED, "client_row": {"id": 1, "name": "qbit"}, "nzo_id": candidate.get("infoHash")}
ix.send_to_download_client = fake_send
ix.set_duration_resolver(lambda p, s: (state["duration_calls"].append(s), 17 * 60)[1])

db = Db()
QP = {"resolutions": [1080, 720]}
dead, other, same = H(1), H(2), H(3)
results = [rel(dead, 0), rel(other, 25, name=NAME.replace("KTR", "OTHER")), rel(same, 9)]  # `same` has the dead one's name and another hash

def go(**kw):
    state["grabbed"].clear()
    return ix.search_and_grab(db, None, None, "stashdb", "s1", "Wet and Clean", "2013-09-08", ["Alexis B"], QP, search_source=Source(results), **kw)

out = go()
check("a running download stops a normal search", out["result"] == ix.RESULT_ALREADY_DOWNLOADING and state["grabbed"] == [])
out = go(exclude_hashes=[dead], replace_job=True)
check("switch: grabbed although a download is running", out["grab"] is not None and len(state["grabbed"]) == 1)
check("switch: the dead torrent is never the choice", dead not in state["grabbed"])
check("switch: the strongest swarm is taken (25 seeders)", state["grabbed"] == [other])
check("the scene's length was asked from the provider", state["duration_calls"] == ["s1"])

state["active"] = False
results[1:2] = []  # only the dead one and the same-named one with another hash are left
out = go(exclude_hashes=[dead], replace_job=True)
check("same name, other hash: still eligible", state["grabbed"] == [same])

results[:] = [rel(dead, 0)]
out = go(exclude_hashes=[dead], replace_job=True)
check("only the dead torrent exists: nothing is grabbed", out["grab"] is None and state["grabbed"] == [])

state["blocked"] = ["hash:" + dead]
results[:] = [rel(dead, 5), rel(same, 3)]
out = go()
check("a hash in the scene's blocklist works the same way", state["grabbed"] == [same])

# the indexers do not answer: a switch takes the best torrent of the last search, a normal search does not
from datetime import datetime, timezone
class DownSource(Source):
    def search(self, q): raise RuntimeError("Read timed out")
saved = []
release_repo.set_search_details_for_scene = lambda db_, p, s_, d: saved.append(d)
last = {"searched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "total": 3, "candidates": [
    {"title": NAME, "indexer": "x", "protocol": "torrent", "info_hash": dead, "seeders": 7, "verdict": "grabbed", "size": 7e8},
    {"title": NAME, "indexer": "x", "protocol": "torrent", "info_hash": same, "seeders": 9, "verdict": "lower_rank", "size": 7e8}]}
release_repo.get_search_details_for_scene = lambda db_, p, s_: dict(last)
state["blocked"] = []
state["grabbed"].clear()
out = ix.search_and_grab(db, None, None, "stashdb", "s1", "Wet and Clean", "2013-09-08", ["Alexis B"], QP, search_source=DownSource([]),
                         exclude_hashes=[dead], replace_job=True)
check("search down + switch: the other torrent of the last log is taken", out["grab"] is not None and state["grabbed"] == [same])
state["grabbed"].clear(); saved.clear(); state["active"] = False
out = ix.search_and_grab(db, None, None, "stashdb", "s1", "Wet and Clean", "2013-09-08", ["Alexis B"], QP, search_source=DownSource([]))
check("search down, normal search: nothing is grabbed from an old log", out["result"] == ix.RESULT_SEARCH_FAILED and state["grabbed"] == [])
check("the failed search keeps the old candidates in the log, marked with their time", saved and saved[-1]["candidates"] == last["candidates"] and saved[-1]["kept_from"] == last["searched_at"])
print("\nALL OK" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
