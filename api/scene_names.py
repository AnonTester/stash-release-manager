"""Scene lists of a movie from scrapers of our own (v0.124.0 AdultDVDEmpire, v0.127.0 IAFD). Stash's own group URL scraper has no scene list, so the page is read
here: ONE request per page (cached for an hour). Per scene: title, length in minutes, performers, the clip URL. Display / suggestion only --
nothing is written to Stash by this module."""
import html
import re
import time

import requests

_HOSTS = ("adultdvdempire.com", "adultempire.com")
_CACHE = {}  # url -> (time, scenes)
_TTL = 3600
_ANCHOR = re.compile(r'<a name="scene_(\d+)" class="anchor"></a>')
_TITLE = re.compile(r"""<h3[^>]*>\s*<a href=['"]([^'"]+)['"][^>]*Label=["']Scene Title["'][^>]*>(.*?)</a>""", re.S)
_MINUTES = re.compile(r'</h3><span class="[^"]*">\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*min)?\s*</span>', re.S)
_STAR = re.compile(r'<a href="/\d+/[^"]*-pornstars\.html">(.*?)</a>', re.S)


def adult_empire_url(urls):
    """The first AdultDVDEmpire (straight) page among a group's URLs, else None."""
    for u in urls or []:
        if any(h in u for h in _HOSTS) and "gay" not in u:
            return u
    return None


def parse(page):
    """Scenes of an AdultDVDEmpire movie page in page order: [{id, n, title, minutes, performers, url}]."""
    parts = _ANCHOR.split(page)
    scenes = []
    for i in range(1, len(parts), 2):
        sid, block = parts[i], parts[i + 1]
        t = _TITLE.search(block)
        if not t:
            continue
        m = _MINUTES.search(block)
        scenes.append({"id": sid, "n": len(scenes) + 1, "title": re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", t.group(2)))).strip(),
                       "minutes": (int(m.group(1) or 0) * 60 + int(m.group(2) or 0)) or None if m else None,
                       "performers": [html.unescape(re.sub(r"\s+", " ", p)).strip() for p in _STAR.findall(block.split("</div></div></div>")[0])],
                       "url": "https://www.adultdvdempire.com" + t.group(1) if t.group(1).startswith("/") else t.group(1)})
    return scenes


def fetch(url):
    """The scenes of the page (ONE request unless cached). Raises on a network/HTTP error."""
    hit = _CACHE.get(url)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"}, cookies={"ageConfirmed": "true"})
    r.raise_for_status()
    scenes = parse(r.text)
    _CACHE[url] = (time.time(), scenes)
    return scenes


def suggest(local, scenes, tolerance=3.0):
    """{local scene id: scene n}: each local scene to the scene of the page with the closest length (within `tolerance` minutes), every
    page scene at most once. Only a hint for the picker."""
    pairs = sorted(((abs(l["minutes"] - s["minutes"]), l["id"], s["n"]) for l in local for s in scenes if s["minutes"] and l["minutes"]
                    and abs(l["minutes"] - s["minutes"]) <= tolerance))
    out, used = {}, set()
    for _, lid, n in pairs:
        if lid not in out and n not in used:
            out[lid] = n
            used.add(n)
    return out


# ---------- IAFD (v0.127.0): scene breakdown = performers per scene + bonus scenes; no titles, no lengths ----------

_IAFD_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


def iafd_url(urls, name, year):
    """The group's own iafd.com title URL when it has one, else the title URL built from name + year (IAFD redirects it to the movie page;
    its search page is blocked for scripts, the title URL is not)."""
    import urllib.parse
    for u in urls or []:
        if "iafd.com/title.rme/" in u:
            return u
    if not name or not year:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"https://www.iafd.com/title.rme/title={urllib.parse.quote_plus(name)}/year={year}/{slug}.htm"


def parse_iafd(page):
    """{"title", "minutes", "studio", "comments", "scenes": [{id, n, kind 'scene'|'bonus', title '', minutes None, performers, url None}]}.
    `n` is the position: scenes 1..k, bonus scenes k+1.. in the order listed."""
    low = page.lower()
    i = low.find("scene breakdowns")
    scenes, comments = [], ""
    if i >= 0:
        seg = page[i:]
        end = min([x for x in (seg.lower().find(m) for m in ("external reviews", "honors and awards", "buy this movie")) if x > 0] or [len(seg)])
        seg = seg[:end]
        text = re.sub(r"<script.*?</script>", "", seg, flags=re.S)
        text = re.sub(r"<(?:tr|/tr|/td|/th|br|/div|/p|/h\d)[^>]*>", "\n", text)
        lines = [html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x))).strip() for x in text.split("\n")]
        lines = [x for x in lines if x]
        mains, bonus = [], []
        k = 1
        while k < len(lines):
            m = re.match(r"^(Scene \d+|Bonus)$", lines[k], re.I)
            if m and k + 1 < len(lines):
                perf = [x.strip() for x in lines[k + 1].split(",") if x.strip()]
                (bonus if m.group(1).lower() == "bonus" else mains).append(perf)
                k += 2
            else:
                k += 1
        for j, perf in enumerate(mains, 1):
            scenes.append({"id": f"iafd-{j}", "n": j, "kind": "scene", "title": "", "minutes": None, "performers": perf, "url": None})
        for j, perf in enumerate(bonus, 1):
            scenes.append({"id": f"iafd-b{j}", "n": len(mains) + j, "kind": "bonus", "title": "", "minutes": None, "performers": perf, "url": None})
        c = re.search(r"Comments(.*?)(?:Honors and Awards|External Reviews|Buy This Movie)", page[i:], re.S)
        if c:
            comments = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c.group(1)))).strip()
    t = re.search(r"<title>(.*?)</title>", page, re.S)
    mm = re.search(r"Minutes</p>\s*<p[^>]*>\s*(\d+)", page, re.S)
    st = re.search(r"Studio</p>\s*<p[^>]*>(.*?)</p>", page, re.S)
    return {"title": re.sub(r"\s*-\s*iafd\.com.*$", "", html.unescape(t.group(1)).strip(), flags=re.I) if t else None,
            "minutes": int(mm.group(1)) if mm else None,
            "studio": html.unescape(re.sub(r"<[^>]+>", "", st.group(1))).strip() if st else None,
            "comments": comments, "scenes": scenes}


_IAFD_CACHE = {}


def fetch_iafd(url):
    """The IAFD movie page parsed (ONE request, followed redirects, cached for an hour); raises when IAFD blocks or has no such page."""
    hit = _IAFD_CACHE.get(url)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    r = requests.get(url, timeout=30, allow_redirects=True,
                     headers={"User-Agent": _IAFD_UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"})
    if r.status_code != 200:
        raise RuntimeError(f"IAFD answered {r.status_code}")
    data = parse_iafd(r.text)
    data["url"] = r.url
    _IAFD_CACHE[url] = (time.time(), data)
    return data
