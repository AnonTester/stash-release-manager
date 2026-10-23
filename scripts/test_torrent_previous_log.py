"""When the indexers do not answer while a dead torrent is replaced (v0.231.7): the best torrent of the last search is added as a magnet link."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import indexer_search as ix

fails = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

H = lambda n: f"{n:040x}"
def cand(n, seeders, verdict="lower_rank", **k):
    return {"title": f"Scene.{n}.1080p", "indexer": "x", "protocol": "torrent", "info_hash": H(n), "seeders": seeders, "peers": 1, "size": 700_000_000,
            "age": 10, "verdict": verdict, **k}
now = datetime.now(timezone.utc)
stamp = lambda hours: (now - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
CFG = {"min_seeders": 1, "plenty": 10, "factor": 3.0}
grabs = []
def fake_grab(db, p, s, c, replace_job=False):
    grabs.append((c, replace_job))
    return {"result": "grabbed", "grab": {"title": c["title"], "nzo_id": c["infoHash"]}}
ix.grab_candidate = fake_grab
ix.history.scene = lambda *a, **k: None

log = {"searched_at": stamp(2), "candidates": [
    cand(1, 5, "grabbed"),                      # the dead one (blocked by its hash)
    cand(2, 3), cand(3, 9), cand(4, 0), cand(5, 20, "title_mismatch"), cand(6, 12, size_note="size does not fit"),
    {**cand(7, 8), "protocol": "usenet", "info_hash": ""}, cand(8, 15, "blocklisted")]}
r = ix._grab_from_previous_log(None, "stashdb", "s1", log, ["hash:" + H(1)], CFG)
c, replace = grabs[-1]
check("the best torrent of the last search: most seeders among the usable ones", r and c["infoHash"] == H(3) and replace is True)
check("added as a magnet of its hash (no indexer link, no key)", c["downloadUrl"].startswith("magnet:?xt=urn:btih:" + H(3)) and "apikey" not in c["downloadUrl"].lower())

grabs.clear()
r = ix._grab_from_previous_log(None, "stashdb", "s1", log, ["hash:" + H(1), "hash:" + H(3)], CFG)
check("a torrent given up before is skipped", grabs[-1][0]["infoHash"] == H(2))

grabs.clear()
r = ix._grab_from_previous_log(None, "stashdb", "s1", {**log, "searched_at": stamp(24 * 8)}, [], CFG)
check("a log older than a week is not used", r is None and not grabs)
r = ix._grab_from_previous_log(None, "stashdb", "s1", {**log, "searched_at": stamp(0.1), "kept_from": stamp(24 * 8)}, [], CFG)
check("the time of the search that FOUND the results counts (kept_from)", r is None and not grabs)

old = {"searched_at": stamp(1), "candidates": [{k: v for k, v in cand(2, 5).items() if k != "info_hash"}]}
check("an older log without hashes gives nothing", ix._grab_from_previous_log(None, "stashdb", "s1", old, [], CFG) is None and not grabs)
check("nothing usable left: None", ix._grab_from_previous_log(None, "stashdb", "s1", {"searched_at": stamp(1), "candidates": [cand(4, 0)]}, [], CFG) is None)

# the log of a search carries the hash (and no link)
e = ix._explain_candidates([{"title": "Scene.9.1080p", "indexer": "x", "protocol": "torrent", "infoHash": H(9), "seeders": 4, "size": 1, "age": 1,
                             "downloadUrl": "http://x/dl?apikey=SECRET", "quality": {"quality": {"resolution": 1080}}}],
                           [1080], "Other", "2020-01-01", [], False, [], None, None)
check("the search log keeps the info hash and no link", e[0].get("info_hash") == H(9) and "SECRET" not in str(e))

print("FAILED" if fails else "all ok")
sys.exit(1 if fails else 0)
