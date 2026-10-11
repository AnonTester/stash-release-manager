"""Possible match (needs review) respects the quality profile's resolutions (v0.205.1)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import indexer_search as ix

def rel(title, res, size=1_500_000_000, age=5):
    return {"title": title, "size": size, "age": age, "indexerId": 1, "indexer": "x",
            "quality": {"quality": {"resolution": res, "source": "web"}}}

fails = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

# a title that is NOT auto-acceptable (date off) but names both performers
scene, date, perfs = "Some Scene", "2026-09-12", ["Anna Example", "Beth Sample"]
r2160 = rel("ExampleStudio.26.10.30.Anna.Example.And.Beth.Sample.XXX.2160p.MP4-GRP", 2160)
r1080 = rel("ExampleStudio.26.10.30.Anna.Example.And.Beth.Sample.XXX.1080p.MP4-GRP", 1080, age=9)
rnone = rel("ExampleStudio.26.10.30.Anna.Example.And.Beth.Sample.XXX.MP4-GRP", None, age=7)

def pick(releases, allowed, unknown=True):
    return ix._select_review_candidate(releases, scene, date, perfs, [], allowed_resolutions=allowed, allow_unknown_resolution=unknown)

c = pick([r2160], [1080, 720])
check("2160p is not offered when the profile accepts 1080p / 720p", c is None)
c = pick([r2160, r1080], [1080, 720])
check("the allowed 1080p one is offered instead", c is not None and "1080p" in c["title"])
c = pick([r2160], [2160, 1080, 720])
check("2160p is offered when the profile accepts it", c is not None and "2160p" in c["title"])
c = pick([rnone], [1080, 720])
check("a name without a resolution stays offered (checked after the download)", c is not None)
c = pick([rnone], [1080, 720], unknown=False)
check("... unless unknown resolutions are not allowed (upgrade searches)", c is None)
c = ix._select_review_candidate([r2160], scene, date, perfs, [])
check("no profile given -> unchanged behaviour", c is not None)
print("\nALL OK" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
