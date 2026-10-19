"""Tests (v0.230.0): the indexer search also uses the SITE behind a scene (its "Studio" link) and the title without a trailing " - <cast>".
Real case (neutral names): a scene credited to a performer-named studio is released as "<Site>.<Performer>.<Title words>".
Run:  venv/bin/python scripts/test_search_site_names.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import indexer_search as ix  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

check("brand from a normal site url", ix._brand_from_url("https://www.example-site.com/watch/1/x") == "examplesite")
check("brand from a sub domain", ix._brand_from_url("https://members.mysite.net/a") == "mysite")
check("brand from a co.uk host", ix._brand_from_url("https://www.shop.co.uk/a") == "shop")
check("no brand from garbage", ix._brand_from_url("not a url") == "" and ix._brand_from_url("") == "")

# the resolver: only "Studio" links count, known names are skipped, nothing without a resolver
ix.set_site_resolver(None)
check("no resolver, no site names", ix.studio_site_names(None, "stashdb", "s1", "Some Creator") == [])
LINKS = [{"url": "https://www.examplesite.com/watch/9/x", "site": "Studio"}, {"url": "https://twitter.com/x", "site": "Twitter"},
         {"url": "https://www.creator.com/a", "site": "Studio"}]
ix.set_site_resolver(lambda provider, sid: LINKS)
names = ix.studio_site_names(None, "stashdb", "s1", "Creator", ["Parent Network"])
check("the site of the Studio link is used, other link types and the studio's own name are not", names == ["examplesite"])
ix.set_site_resolver(lambda provider, sid: [{"url": "https://www.bigshop.com/a", "site": "Studio"}])
check("a site that is part of the studio's name adds nothing", ix.studio_site_names(None, "stashdb", "s1", "BigShop Extra") == []
      and ix.studio_site_names(None, "stashdb", "s1", ["Parent", "Big Shop Network"]) == [])
def boom(provider, sid): raise RuntimeError("down")  # noqa: E704
ix.set_site_resolver(boom)
check("a failing lookup never breaks the search", ix.studio_site_names(None, "stashdb", "s1", "Creator") == [])

check("the title loses its trailing cast", ix._title_without_cast("Quiet student reads late - Jane Roe & John Doe", ["Jane Roe", "John Doe"]) == "Quiet student reads late")
check("a dash that is not followed by the cast stays", ix._title_without_cast("Part 2 - The Return", ["Jane Roe"]) == "")
check("a title without a dash stays", ix._title_without_cast("Plain Title", ["Jane Roe"]) == "")

title = "Quiet student reads late - Jane Roe & John Doe"
q = ix._build_queries(title, ["Jane Roe", "John Doe"], "Creator Name", "2026-10-18", (), ["examplesite"])
check("full title first, then the title without the cast", q[0] == title and q[1] == "Quiet student reads late")
check("the site with the first performer", "examplesite Jane Roe" in q)
check("the site with the date", "examplesite 26.10.18" in q)
check("the old queries are still there", "Jane Roe John Doe" in q and "CreatorName 26.10.18" in q)
check("without site names nothing new is invented", "examplesite Jane Roe" not in ix._build_queries(title, ["Jane Roe", "John Doe"], "Creator Name", "2026-10-18"))
check("no duplicates", len({x.lower() for x in q}) == len(q))

# the release that was found by hand is accepted by the automatic judge
ok = ix._candidate_plausible(title, "2026-10-18", ["Jane Roe", "John Doe"], "ExampleSite.Jane.Roe.Quiet.Student.Reads.Late.1080p")
check("the release named after the site is plausible", ok)

print("\nFAILED: " + ", ".join(FAILS) if FAILS else "\nall ok")
sys.exit(1 if FAILS else 0)
