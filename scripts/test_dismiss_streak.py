"""Dismiss streak per studio (v0.216.0). Run: venv/bin/python scripts/test_dismiss_streak.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from db import Database
import release_repo, dismiss_streak as ds

res = []
def check(name, cond):
    res.append(bool(cond)); print(("ok   " if cond else "FAIL ") + name)

db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
def scene(i, studio="st1", name="Sub Studio"):
    return {"id": f"s{i}", "title": f"T{i}", "date": "2026-10-01", "created": "2026-10-01T00:00:00Z", "studio": {"id": studio, "name": name}}
with db.connect() as c:
    for i in range(1, 31):
        release_repo.insert_release(db, c, scene(i), "stashdb", "studio", "parent")
    release_repo.insert_release(db, c, scene(40, "st2", "Other"), "stashdb", "performer", "p1")  # performer hit: never counted

def dismiss(i):
    prev = release_repo.snapshot_scene_rows(db, "stashdb", f"s{i}")
    release_repo.set_status_for_scene(db, "stashdb", f"s{i}", "dismissed")
    return ds.on_dismiss(db, "stashdb", f"s{i}", prev), prev

sugg = [dismiss(i)[0] for i in range(1, 10)]
check("nine dismisses: no offer", all(s is None for s in sugg))
s10, prev10 = dismiss(10)
check("the tenth offers to hide the studio (by the scene's own studio)", s10 and s10["studio_id"] == "st1" and s10["count"] == 10 and s10["name"] == "Sub Studio")
more = [dismiss(i)[0] for i in range(11, 20)]
check("no second offer for dismisses 11-19", all(m is None for m in more))
s20 = dismiss(20)[0]
check("second offer after ten more (the 20th)", s20 and s20["count"] == 20)
check("and not again for the 21st", dismiss(21)[0] is None)
# wishlist resets
release_repo.set_status_for_scene(db, "stashdb", "s25", "sent_to_whisparr")
with db.connect() as c:
    row = c.execute("SELECT count, asked_at_count FROM studio_dismiss_streak WHERE studio_id = 'st1'").fetchone()
check("a wishlist add of the studio resets the streak", row["count"] == 0 and row["asked_at_count"] == 0)
# undo takes one back
for i in range(26, 29): dismiss(i)
prev = release_repo.snapshot_scene_rows(db, "stashdb", "s29")
release_repo.set_status_for_scene(db, "stashdb", "s29", "dismissed"); ds.on_dismiss(db, "stashdb", "s29", prev)
ds.on_restore(db, prev)
with db.connect() as c:
    check("undo takes one back", c.execute("SELECT count FROM studio_dismiss_streak WHERE studio_id='st1'").fetchone()["count"] == 3)
# not counted: performer hit, wishlist-row dismiss
prev = release_repo.snapshot_scene_rows(db, "stashdb", "s40")
release_repo.set_status_for_scene(db, "stashdb", "s40", "dismissed")
check("performer hit is not counted", ds.on_dismiss(db, "stashdb", "s40", prev) is None)
check("a dismiss of a wishlist row is not counted", ds.on_dismiss(db, "stashdb", "s30", [{"entity_type": "studio", "entity_stashdb_id": "parent", "status": "sent_to_whisparr"}]) is None)
# hidden studio forgotten
release_repo.mute_studio(db, "st1", "Sub Studio")
with db.connect() as c:
    check("hiding the studio drops its counter", c.execute("SELECT COUNT(*) n FROM studio_dismiss_streak WHERE studio_id='st1'").fetchone()["n"] == 0)
check("a hidden studio is no longer counted", ds.on_dismiss(db, "stashdb", "s30", [{"entity_type": "studio", "entity_stashdb_id": "parent", "status": "new"}]) is None)

# seed from history: ten dismisses, then a wishlist add, then three dismisses -> 3
import history
db2 = Database(os.path.join(tempfile.mkdtemp(), "t2.sqlite3"))
with db2.connect() as c:
    for i in range(1, 16):
        release_repo.insert_release(db2, c, scene(i), "stashdb", "studio", "parent")
    for i in range(1, 11):
        c.execute("INSERT INTO history (at, level, kind, category, message, provider, scene_id) VALUES (?, 'info', 'history', 'dismiss', 'Dismissed', 'stashdb', ?)", (f"2026-10-10 10:00:{i:02d}", f"s{i}"))
    c.execute("INSERT INTO history (at, level, kind, category, message, provider, scene_id) VALUES ('2026-10-10 11:00:00', 'info', 'history', 'wishlist', 'Added to the wishlist', 'stashdb', 's11')")
    for i in (12, 13, 14):
        c.execute("INSERT INTO history (at, level, kind, category, message, provider, scene_id) VALUES (?, 'info', 'history', 'dismiss', 'Dismissed', 'stashdb', ?)", (f"2026-10-10 12:00:{i:02d}", f"s{i}"))
    c.execute("DROP TABLE studio_dismiss_streak")
    ds.ensure_table(c); ds.seed_from_history(c)
    check("seed: replays the history, the wishlist add resets (3 left)", c.execute("SELECT count FROM studio_dismiss_streak WHERE studio_id='st1'").fetchone()["count"] == 3)
sys.exit(0 if all(res) else 1)
