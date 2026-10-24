"""Tests (v0.236.0): a feed request that fails (timeout on a deep page) falls back to the per-entry scan like FeedIncomplete, and a clean
fallback advances the provider's feed watermark (never backwards); a fallback with failures keeps it.
Run:  venv/bin/python scripts/test_feed_fallback.py"""
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import watcher  # noqa: E402
import watchlist_repo  # noqa: E402
from db import Database  # noqa: E402
from stashdb_client import FeedIncomplete  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))
watchlist_repo.add_to_watchlist(db, "theporndb", "studio", "uuid-1", "Example Site", native_id=42)
watchlist_repo.set_watermark(db, "theporndb", "studio", "uuid-1", "2026-10-18T07:00:00.000000Z")
OLD = "2026-10-18T07:01:49.000000Z"

class Client:
    def __init__(self, exc): self.exc = exc
    def fetch_recent_scenes(self, since):
        raise self.exc
        yield  # a generator, like the real one

calls = []
result = {"scanned": 1, "new_baselines": 0, "new_releases": 3, "failed": []}
watcher.run_scan = lambda db_, clients, delay, cb, auto, entries=None: calls.append(entries) or dict(result, failed=list(result["failed"]))

def run(exc, failed=None):
    calls.clear()
    result["failed"] = failed or []
    watchlist_repo.set_feed_watermark(db, "theporndb", OLD)
    return watcher.run_feed_scan(db, {"theporndb": Client(exc)}, delay=0)

# 1. a timeout in the feed: per-entry fallback, watermark advanced, the provider is not reported as failed
s = run(TimeoutError("Read timed out"))
wm = watchlist_repo.get_feed_watermark(db, "theporndb")
check("timeout -> the entries are scanned one by one", len(calls) == 1 and calls[0][0]["stashdb_id"] == "uuid-1")
check("timeout -> counted as a fallback, not a failure", s["feed"]["theporndb"]["fallback"] == "per_entry" and s["failed"] == [])
check("clean fallback advances the watermark to about now", wm > OLD and wm.endswith(".000000Z")
      and (datetime.now(timezone.utc) - datetime.strptime(wm, "%Y-%m-%dT%H:%M:%S.000000Z").replace(tzinfo=timezone.utc)).total_seconds() < 3600)
check("the fallback's releases are counted", s["new_releases"] == 3)

# 2. FeedIncomplete behaves the same
s = run(FeedIncomplete("didn't reach"))
check("feed incomplete -> fallback and advance", len(calls) == 1 and watchlist_repo.get_feed_watermark(db, "theporndb") > OLD)

# 3. a fallback with a failed entry keeps the watermark and reports the failure
s = run(TimeoutError("x"), failed=[{"provider": "theporndb", "entity_type": "studio", "name": "Example Site", "error": "boom"}])
check("failed fallback keeps the old watermark", watchlist_repo.get_feed_watermark(db, "theporndb") == OLD)
check("failed fallback reports the entry", len(s["failed"]) == 1)

# 4. never backwards
future = "2099-01-01T00:00:00.000000Z"
calls.clear(); result["failed"] = []
watchlist_repo.set_feed_watermark(db, "theporndb", future)
watcher._advance_feed_watermark(db, "theporndb")
check("the watermark never moves backwards", watchlist_repo.get_feed_watermark(db, "theporndb") == future)

print("\nALL OK" if not FAILS else "\nFAILED: %s" % FAILS)
sys.exit(1 if FAILS else 0)
