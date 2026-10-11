"""Test (v0.179.0): GET /setup/status against a throw-away database. Run:  venv/bin/python scripts/test_setup_status.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
for k in ("STASH_URL", "STASHDB_API_KEY", "PROWLARR_URL", "PROWLARR_API_KEY", "WHISPARR_URL", "WHISPARR_API_KEY", "THEPORNDB_API_KEY"):
    os.environ.pop(k, None)

import download_client_repo  # noqa: E402
import indexer_repo  # noqa: E402
import quality_profile_repo  # noqa: E402
from db import Database  # noqa: E402
from routers import settings as settings_router  # noqa: E402
from routers import setup  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)


db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
settings_router._db = db
setup.init_setup_router(db)
def st():
    r = setup.status()
    return {s["key"]: s for s in r["steps"]}, r
def put(k, v):  # noqa: E302
    with db.connect() as c: c.execute("INSERT OR REPLACE INTO setting (key, value) VALUES (?, ?)", (k, v))

steps, r = st()
with db.connect() as c: seeded_profiles = c.execute("SELECT COUNT(*) FROM quality_profile").fetchone()[0]
check("fresh install: not complete, the five required steps are missing (quality only if no default profile is seeded)",
      not r["complete"] and {"Stash", "StashDB", "Search", "Downloads"} <= set(r["missing"]))
check("fresh install: optional steps are not in 'missing'", "ThePornDB" not in r["missing"] and "Whisparr" not in r["missing"])
check("fresh install: the Stash step points at the setup guide, not at plugin settings",
      "setup guide" in steps["stash"]["detail"] and "plugin's settings" not in steps["stash"]["detail"])
put("stash_url", "http://stash:9999"); put("stashdb_api_key", "k")
steps, r = st()
check("Stash URL + StashDB key: those two are done", steps["stash"]["done"] and steps["stashdb"]["done"])
indexer_repo.create(db, name="Ix", type="newznab", url="http://ix", api_path="/api", api_key="x", categories="6000", priority=25, enabled=True)
steps, r = st()
check("an enabled indexer makes Search done", steps["search"]["done"])
download_client_repo.create(db, "SAB", "sabnzbd", "usenet", 1, True, "http://sab", "key", "", "", "")
steps, r = st()
check("an enabled download client makes Downloads done", steps["download"]["done"])
if not seeded_profiles:
    quality_profile_repo.create(db, "HD", [1080, 720], 720)
steps, r = st()
check("complete when all required steps are done", r["complete"] and r["missing"] == [])
check("optional ThePornDB / Whisparr stay open without breaking 'complete'", not steps["theporndb"]["done"] and not steps["whisparr"]["done"] and r["complete"])
put("theporndb_api_key", "t")
check("ThePornDB key marks that optional step done", st()[0]["theporndb"]["done"])
import metadata_provider_repo  # noqa: E402
steps, r = st()
check("other stash-boxes: optional, open while no provider exists, never blocks 'complete'",
      "stashboxes" in steps and not steps["stashboxes"]["done"] and not steps["stashboxes"]["required"] and r["complete"]
      and "Other stash-boxes" not in r["missing"])
metadata_provider_repo.create(db, "javstash", "JAVStash", "https://javstash.org/graphql", "k", True)
steps, r = st()
check("other stash-boxes: done once a provider is added, named in the detail", steps["stashboxes"]["done"] and "JAVStash" in steps["stashboxes"]["detail"])
print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
