"""Group scrape (v0.212.0): length text, scraper-by-address check, source check, AdultDVDEmpire search-card parser."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from library_movies import Movies

res = []
def check(name, cond):
    res.append(bool(cond)); print(("ok   " if cond else "FAIL ") + name)

S = Movies._seconds
check("h:mm:ss", S("2:49:00") == 10140)
check("mm:ss", S("54:00") == 3240)
check("no length", S("") is None and S(None) is None and S("abc") is None and S("0:00:00") is None)
m = Movies.__new__(Movies)
m._scrapers_cache = (__import__("time").time(), [{"id": "AdultEmpire", "name": "AdultEmpire", "group": {"urls": ["adultdvdempire.com", "adultempire.com"], "supported_scrapes": ["URL"]}}])
check("address of an installed scraper -> its name", m.scraper_for_url("https://www.adultdvdempire.com/5091475/x.html") == "AdultEmpire")
check("foreign address -> no scraper", m.scraper_for_url("https://evil.example/x") is None)
check("not http(s) -> no scraper", m.scraper_for_url("file:///etc/passwd") is None and m.scraper_for_url("javascript:adultempire.com") is None)
try:
    m.group_search("nope", "x"); check("unknown source refused", False)
except ValueError:
    check("unknown source refused", True)
card = '''<div class="product-card" id="card5091475"><div class="boxcover-container"><a href=" /5091475/x.html" class="boxcover"><img
 src="https://imgs4cdn.adultempire.com/movie/5091475?w=190&h=270&fit=cover" class="img-responsive"/></a></div><div class="product-details"><div class="product-details__item-title"><a href="/5091475/anal-icons-vol-5-porn-movies.html" Category="GridViewDVD"
 title="Anal Icons Vol. 5" Label="Title">Anal Icons Vol. 5</a></div><div class="product-details__product-type">
 DVD
 </div><div class="item-info p-t-1">'''
found = Movies._ADE_CARD.findall(card)
check("card found", len(found) == 1 and found[0][0] == "5091475")
print("\nALL OK" if all(res) else "\nFAILED"); sys.exit(0 if all(res) else 1)
