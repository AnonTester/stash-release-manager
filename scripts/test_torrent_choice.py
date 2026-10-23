"""Torrent choice (v0.231.0): seeders vs. resolution, estimated resolution for unlabelled names, size that does not fit, hash blocklist."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import indexer_search as ix

fails = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

TITLE, DATE, PERF = "Wet and Clean", "2013-09-08", ["Alexis B"]
ALLOWED = [1080, 720]
D = 17 * 60
H = lambda n: f"{n:040x}"


def rel(tag, res, size=700_000_000, seeders=None, protocol="torrent", age=100, h=None, name=None, indexer=1):
    label = f"{res}p." if res else ""
    title = name or f"Joymii.13.09.08.Alexis.B.Wet.And.Clean.XXX.{label}MP4-{tag}"
    r = {"title": title, "size": size, "age": age, "indexerId": indexer, "indexer": "x", "protocol": protocol, "downloadUrl": f"http://x/{tag}",
         "quality": {"quality": {"resolution": res, "source": "web"}}}
    if protocol == "torrent":
        r["seeders"] = seeders
        r["infoHash"] = h or H(abs(hash(tag)) % (16 ** 12))
    return r


def pick(releases, **kw):
    kw.setdefault("allow_unknown_resolution", True)
    best = ix._select_best(releases, ALLOWED, {}, TITLE, DATE, PERF, **kw)
    return best["title"].rsplit("-", 1)[-1] if best else None

# -- seeders vs. resolution
check("plenty of seeders at the best resolution -> the best", pick([rel("A", 1080, seeders=40), rel("B", 720, seeders=400)]) == "A")
check("few seeders at the best, many at 720p -> 720p", pick([rel("A", 1080, seeders=2), rel("B", 720, seeders=30)]) == "B")
check("few at both -> the best resolution", pick([rel("A", 1080, seeders=2), rel("B", 720, seeders=4)]) == "A")
check("exactly the factor (3x) is enough", pick([rel("A", 1080, seeders=2), rel("B", 720, seeders=6)]) == "B")
check("just below the factor is not", pick([rel("A", 1080, seeders=2), rel("B", 720, seeders=5)]) == "A")
check("inside a resolution: most seeders wins", pick([rel("A", 1080, seeders=12), rel("B", 1080, seeders=80), rel("C", 1080, seeders=30)]) == "B")
check("seeders beat indexer priority / age inside the group", ix._select_best(
    [rel("A", 1080, seeders=12, indexer=1, age=1), rel("B", 1080, seeders=80, indexer=2, age=900)], ALLOWED, {1: 1, 2: 50}, TITLE, DATE, PERF)["title"].endswith("B"))
check("0 seeders is a last resort", pick([rel("A", 1080, seeders=0), rel("B", 720, seeders=1)]) == "B")
check("0 seeders is still taken when nothing else exists", pick([rel("A", 1080, seeders=0)]) == "A")
check("unknown seeders count as the minimum, not as dead", pick([rel("A", 1080, seeders=None), rel("B", 720, seeders=1)]) == "A")
check("usenet still wins at equal resolution", pick([rel("A", 1080, seeders=500), rel("N", 1080, protocol="usenet")]) == "N")
check("torrent with few seeders does not beat a usenet 720p over a usenet-less 1080p", pick([rel("A", 1080, seeders=1), rel("N", 720, protocol="usenet"), rel("B", 720, seeders=50)]) == "N")
check("custom thresholds are used", ix._select_best([rel("A", 1080, seeders=2), rel("B", 720, seeders=9)], ALLOWED, {}, TITLE, DATE, PERF,
                                                     torrent_cfg={"min_seeders": 1, "plenty": 10, "factor": 10.0})["title"].endswith("A"))

# -- unlabelled names, estimated from the length
unl = lambda tag, size, seeders=20, name=None: rel(tag, None, size=size, seeders=seeders, name=name or f"Joymii.13.09.08.Alexis.B.Wet.And.Clean.XXX.MP4-{tag}")
check("unlabelled, 4 Mbps -> estimated 720p, taken", pick([unl("U", int(4e6 / 8 * D))], duration_s=D) == "U")
check("a named 720p beats an estimated 720p of the same rank", pick([unl("U", int(4e6 / 8 * D), seeders=100), rel("N", 720, seeders=100)], duration_s=D) == "N")
check("estimated 1080p (7 Mbps) ranks above a named 720p", pick([unl("U", int(7e6 / 8 * D), seeders=100), rel("N", 720, seeders=100)], duration_s=D) == "U")
check("without a length an unlabelled name stays 'unknown' (after the named ones)", pick([unl("U", int(7e6 / 8 * D)), rel("N", 720, seeders=20)]) == "N")

# -- sizes that do not fit
check("3 GB for 17 minutes (labelled 1080p) is the last resort", pick([rel("BIG", 1080, size=3_000_000_000, seeders=50), rel("OK", 720, size=500_000_000, seeders=5)], duration_s=D) == "OK")
check("... but is taken when it is all there is", pick([rel("BIG", 1080, size=3_000_000_000, seeders=50)], duration_s=D) == "BIG")
check("30 MB labelled 1080p is mislabelled too", pick([rel("TINY", 1080, size=30_000_000, seeders=50), rel("OK", 720, size=500_000_000, seeders=5)], duration_s=D) == "OK")
check("no length known: sizes are not judged", pick([rel("BIG", 1080, size=3_000_000_000, seeders=50), rel("OK", 720, size=500_000_000, seeders=5)]) == "BIG")

# -- blocklist by hash / link
a, b = rel("A", 1080, seeders=30, h=H(1), name="Joymii.13.09.08.Alexis.B.Wet.And.Clean.XXX.1080p.MP4-SAME"), rel("B", 1080, seeders=10, h=H(2), name="Joymii.13.09.08.Alexis.B.Wet.And.Clean.XXX.1080p.MP4-SAME")
check("the blocked hash is skipped, the same name with another hash is taken", ix._select_best([a, b], ALLOWED, {}, TITLE, DATE, PERF, rejected_titles=["hash:" + H(1)])["infoHash"] == H(2))
check("without the block the stronger swarm is taken", ix._select_best([a, b], ALLOWED, {}, TITLE, DATE, PERF)["infoHash"] == H(1))
check("the hash in an entry is case-insensitive", ix._select_best([a, b], ALLOWED, {}, TITLE, DATE, PERF, rejected_titles=["hash:" + H(1).upper()])["infoHash"] == H(2))
m = dict(a, infoHash=None, magnetUrl=f"magnet:?xt=urn:btih:{H(1).upper()}&dn=x")
check("hash read from a magnet link", ix.release_hash(m) == H(1))
check("a plain title entry still blocks both names (old behaviour)", ix._select_best([a, b], ALLOWED, {}, TITLE, DATE, PERF, rejected_titles=[a["title"]]) is None)
check("exact: entry still blocks the name only by name (both share it)", ix._select_best([a, b], ALLOWED, {}, TITLE, DATE, PERF, rejected_titles=["exact:" + a["title"]]) is None)
l1 = dict(a, downloadUrl="http://prowlarr/5/download?apikey=SECRET&link=abc")
check("link entry matches without the key", ix._is_rejected(l1["title"], ix._split_rejected(["link:" + ix.link_key(l1["downloadUrl"])]), dict(l1, downloadUrl="http://prowlarr/5/download?link=abc&apikey=OTHER")))
check("link_key drops keys, keeps the rest", "SECRET" not in ix.link_key(l1["downloadUrl"]) and "link=abc" in ix.link_key(l1["downloadUrl"]))

# -- duplicate guard: hash only
class Client:
    def __init__(self, torrents): self.t = torrents
    def get_torrents(self, category=None): return self.t
same_name_other_hash = Client([{"hash": H(9), "name": a["title"]}])
check("same name, other hash: not a duplicate", ix._torrent_duplicate_guard(same_name_other_hash, {"title": a["title"], "infoHash": H(1)}) is None)
check("same hash: duplicate", ix._torrent_duplicate_guard(Client([{"hash": H(1), "name": "x"}]), {"title": "y", "infoHash": H(1)}) is not None)
check("no hash known: left to the client's add()", ix._torrent_duplicate_guard(same_name_other_hash, {"title": a["title"]}) is None)

# -- the search log carries seeders and the estimates
log = ix._explain_candidates([unl("U", int(4e6 / 8 * D)), rel("BIG", 1080, size=3_000_000_000, seeders=7)], ALLOWED, TITLE, DATE, PERF, False, [], None, None,
                             allow_unknown_resolution=True, duration_s=D)
by = {e["title"].rsplit("-", 1)[-1]: e for e in log}
check("log: estimated resolution and torrent facts", by["U"].get("est_resolution") == 720 and by["U"]["protocol"] == "torrent" and by["BIG"]["seeders"] == 7)
check("log: size note on a mismatch", "does not fit" in by["BIG"].get("size_note", "") and "size_note" not in by["U"])
print("\nALL OK" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
