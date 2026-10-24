"""Tests (v0.237.1): a scene that is being upgraded (release.upgrade_of) is held to "only a release above the library file" by every search.
Run:  venv/bin/python scripts/test_upgrade_filter.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import release_repo  # noqa: E402
import upgrades  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))
def scene(pid, upgrade_of=None):
    with db.connect() as conn:
        release_repo.insert_release(db, conn, {"id": pid, "title": "T " + pid, "date": "2026-01-01", "created": "2026-01-01", "performers": []},
                                    "stashdb", "studio", "e1", status="sent_to_whisparr")
        if upgrade_of:
            conn.execute("UPDATE release SET upgrade_of = ? WHERE scene_stashdb_id = ?", (upgrade_of, pid))
def cand(pid, reason, height, path):
    with db.connect() as conn:
        conn.execute("""INSERT INTO upgrade_candidate (scene_id, file_path, scene_title, provider, provider_scene_id, reason, status, cur_width, cur_height, cur_size)
                        VALUES (?, ?, 'T', 'stashdb', ?, ?, 'wanted', 1920, ?, 1000)""", (pid, path, pid, reason, height))
def rel(res, title="Some.Release"):
    return {"title": title, "quality": {"quality": {"resolution": res}}, "size": 1}

scene("plain")
check("an ordinary wishlist scene has no upgrade filter", upgrades.filter_for_scene(db, "stashdb", "plain") is None)

scene("below", "/m/below.mp4"); cand("below", "below_cutoff", 720, "/m/below.mp4")
f = upgrades.filter_for_scene(db, "stashdb", "below")
check("below cutoff: a higher resolution passes", f(rel(1080)) is True)
check("below cutoff: the same or a lower resolution (the last-resort SD) is refused", f(rel(720)) is False and f(rel(480)) is False)
check("below cutoff: a release without a resolution is refused", f({"title": "X", "quality": {}}) is False)

scene("vr", "/m/vr.mp4"); cand("vr", "vr_low_quality", 1600, "/m/vr.mp4")
f = upgrades.filter_for_scene(db, "stashdb", "vr")
check("VR low quality: a name that proves a higher resolution passes", f(rel(0, "Studio.Scene.8K.VR180_LR")) is True)
check("VR low quality: a name that does not is refused", f(rel(0, "Studio.Scene.1080p.VR")) is False)

scene("again", "/m/again.mp4"); cand("again", "readd", 720, "/m/again.mp4")
f = upgrades.filter_for_scene(db, "stashdb", "again")
check("a re-add accepts what the normal search accepts, but the filter exists (so no last resort)", f is not None and f(rel(480)) is True)

scene("orphan", "/m/orphan.mp4")
f = upgrades.filter_for_scene(db, "stashdb", "orphan")
check("an upgrade row without a candidate: accept-all filter, still no last resort", f is not None and f(rel(480)) is True)

print("\nALL OK" if not FAILS else "\nFAILED: %s" % FAILS)
sys.exit(1 if FAILS else 0)
