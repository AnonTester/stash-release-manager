"""Tests of the catalog load for new watchlist entries (v0.198.1) and of the per-box query delay / free provider colour, against a THROWAWAY database
and a fake StashDB client. Run:  venv/bin/python scripts/test_catalog_on_watch.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import backlog_repo  # noqa: E402
import metadata_provider_repo as mp  # noqa: E402
import watchlist_repo  # noqa: E402
from db import Database  # noqa: E402
from routers import backlog, metadata_providers  # noqa: E402
from routers.settings import DEFAULTS  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)


class FakeStashDB:
    delay = 0
    def __init__(self): self.fetched = []
    def count_scenes_by_year(self, et, sid): return {2015: 3, 2020: 2, 2024: 0, 2025: 4}
    def fetch_scenes_for_year(self, et, sid, year):
        self.fetched.append(year)
        return [{"id": f"s{year}-{i}", "title": "t", "date": f"{year}-05-01", "created": f"{year}-05-02T00:00:00Z", "studio": {}, "performers": []} for i in range(2)]


class FakeStash:
    def library_heights_by_stash_ids(self, ids): return {}


db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))
fake = FakeStashDB()
backlog.init_backlog_router(db, lambda provider: fake, lambda: FakeStash(), lambda: None)

check("setting default is off", DEFAULTS["catalog_on_watch"] == "off")
check("all years with scenes, newest first", backlog.catalog_years("stashdb", "studio", "ST1") == [2025, 2020, 2015])

with db.connect() as conn:
    conn.execute("INSERT OR REPLACE INTO setting (key, value) VALUES ('default_window_from', '2019-06-01')")
check("global From limits the years", backlog.catalog_years("stashdb", "studio", "ST1") == [2025, 2020])

res = backlog.load_catalog("stashdb", "studio", "ST1", "Test")
check("load_catalog fetched 2025 and 2020", res.get("years") == [2025, 2020] and fake.fetched == [2025, 2020])
check("loaded years are not fetched again", backlog.catalog_years("stashdb", "studio", "ST1") == [])

# provider colour: a new provider gets a colour nobody uses
used = ["#2b6cb0", "#d69e2e", metadata_providers._AUTO_COLORS[0]]
check("auto colour skips used ones", metadata_providers._auto_color("x", used) == metadata_providers._AUTO_COLORS[1])

# per-box query delay
mp.create(db, "fansdb", "FansDB", "https://x/graphql", "k")
check("delay NULL by default", mp.get_one(db, "fansdb")["query_delay"] is None)
check("delay stored", mp.update(db, "fansdb", query_delay="1.5")["query_delay"] == 1.5)
check("delay reset", mp.update(db, "fansdb", query_delay="")["query_delay"] is None)
check("favorites interval stored", mp.update(db, "fansdb", favorites_interval="12")["favorites_interval"] == 12.0)
check("favorites interval reset", mp.update(db, "fansdb", favorites_interval="")["favorites_interval"] is None)
try:
    mp.update(db, "fansdb", query_delay="-1"); check("negative delay rejected", False)
except mp.InvalidKeyError:
    check("negative delay rejected", True)

# favorites import for an added box: matches by THAT box's endpoint
import stashdb_favorites  # noqa: E402
class FavClient:
    def favorited_studio_ids(self): return ["B1"]
    def favorited_performer_ids(self): return ["P1"]
class FavStash:
    seen = []
    def local_studio_ids_by_stashdb_id(self, endpoint=None): self.seen.append(endpoint); return {"B1": 7}
    def local_performer_ids_by_stashdb_id(self, endpoint=None): return {"P1": 9}
    def favorited_studios(self): return []
    def favorited_performers(self): return []
    def set_studios_favorite(self, ids): self.done_s = ids; return (1, 0)
    def set_performers_favorite(self, ids): self.done_p = ids; return (1, 0)
fs = FavStash()
r = stashdb_favorites.import_stashdb_favorites(FavClient(), fs, endpoint="https://fansdb.cc/graphql")
check("box favorites: endpoint passed, entities favorited", fs.seen == ["https://fansdb.cc/graphql"] and fs.done_s == [7] and fs.done_p == [9] and r["studios_favorited"] == 1)

# Prowlarr download client prefill: URL assembled, no key taken over
from routers.download_clients import prowlarr_clients_from_fields  # noqa: E402
pc = prowlarr_clients_from_fields([
    {"name": "Sab", "implementation": "Sabnzbd", "enable": True,
     "fields": [{"name": "host", "value": "nas"}, {"name": "port", "value": 8080}, {"name": "useSsl", "value": True}, {"name": "urlBase", "value": "/sab/"}, {"name": "apiKey", "value": "****"}]},
    {"name": "Other", "implementation": "Deluge", "fields": []}])
check("prowlarr prefill: url built, unusable client skipped, no key", len(pc) == 1 and pc[0]["url"] == "https://nas:8080/sab" and "api_key" not in pc[0])

print("FAILED: %s" % FAILS if FAILS else "all ok")
sys.exit(1 if FAILS else 0)
