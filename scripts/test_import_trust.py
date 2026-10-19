"""Tests (v0.228.0): Import folder "Import anyway" -- a scene whose provider cannot confirm the file by fingerprint is moved only when YOU
name it, and only for the fingerprint reasons. Fakes, no network.   Run:  venv/bin/python scripts/test_import_trust.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import history  # noqa: E402
import library_import  # noqa: E402
from library_import import Importer  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

events = []
history.add = lambda *a, **k: events.append((a, k))

check("fingerprint reasons are trustable", all(r.startswith(library_import._TRUSTABLE) for r in (
    "the provider has no fingerprints for this scene", "fingerprints don't match (phash distance 24)", "no local phash yet", "fingerprint lookup failed", "no exact fingerprint match")))
check("other reasons are not", not any(r.startswith(library_import._TRUSTABLE) for r in (
    "scene has 2 files", "target name already exists", "no StashDB/ThePornDB id", "the provider scene is already held by local scene 5")))

def item(sid, kind, how, can_trust):
    return {"scene_id": sid, "file_id": f"f{sid}", "title": f"T{sid}", "path": f"/data/Scenes/x{sid}.mp4", "new_path": None, "kind": kind,
            "how": how, "provider": "theporndb", "provider_id": f"p{sid}", "can_trust": can_trust}

plan = {"root": "/data/Scenes", "items": [item("1", "review", "the provider has no fingerprints for this scene", True),
                                          item("2", "review", "the provider scene is already held by local scene 9 -- a duplicate?", False),
                                          item("3", "review", "the provider has no fingerprints for this scene", True)]}
applied = []
imp = object.__new__(Importer)
imp.queue_guard = lambda force: None
imp.renamer_guard = lambda ignore: None
imp.analyse = lambda root: plan
imp._scenes_in = lambda root: [{"id": i, "files": [{"id": f"f{i}"}]} for i in (1, 2, 3)]
def plan_target(it, scene, f): it["new_path"] = f"/data/scenes/Studio/{it['title']}.mp4"  # noqa: E704
imp._plan_target = plan_target
def apply_items(label, items): applied.extend(items); return {"done": items, "failed": [], "leftovers": []}  # noqa: E704
imp._apply_items = apply_items

r = imp.apply("/data/Scenes", ["1", "2", "3"])
check("without trust nothing that is not verified moves", applied == [])
r = imp.apply("/data/Scenes", ["1", "2", "3"], trust=["1", "2"])
check("trusted fingerprint failure is moved", [i["scene_id"] for i in applied] == ["1"] and applied[0]["new_path"].endswith("T1.mp4"))
check("a scene that is only asked for, not trusted, stays", all(i["scene_id"] != "3" for i in applied))
check("a non-fingerprint review reason is not overruled", all(i["scene_id"] != "2" for i in applied))
check("History records the waiver", any("without fingerprint check" in a[3] for a, k in events))
check("the result is the normal one", [i["scene_id"] for i in r["done"]] == ["1"])

# the "near phash, file older than the release" demotion offers the override too, and the override skips that check
imp2 = object.__new__(Importer)
imp2.stash = type("S", (), {"stashdb_endpoint": "sdb", "theporndb_endpoint": "tpdb"})()
imp2._provider_scene = lambda scene, sid=None: ({"title": "T", "date": "2020-01-01"}, "Studio")
imp2.expected_path = lambda a, f, st: "/data/scenes/Studio/T.mp4"
imp2.to_local = lambda p: "/nonexistent" + p
library_import._older_than_release = lambda f, d: True
it = {"how": "phash (distance 8)", "kind": "review", "provider": "theporndb", "provider_id": "p", "stashdb_id": None, "scene_id": "1", "path": "/data/Scenes/a.mp4"}
imp2._plan_target(it, {"stash_ids": [], "tags": []}, {"path": "/data/Scenes/a.mp4"})
check("older than the release: review with can_trust", it["kind"] == "review" and it["can_trust"] is True and not it.get("new_path"))
it2 = {**it, "how": "confirmed by you (fingerprint not verified)", "kind": "ready"}
imp2._plan_target(it2, {"stash_ids": [], "tags": []}, {"path": "/data/Scenes/a.mp4"})
check("... and the confirmed one is placed", it2["kind"] == "ready" and it2["new_path"] == "/data/scenes/Studio/T.mp4")

# the scene's metadata is saved before the provider's data is applied
import json  # noqa: E402
import library_check  # noqa: E402
SCENE = {"id": "9", "title": "Hand made title", "performers": [{"id": "1", "name": "Somebody"}], "studio": {"id": "2", "name": "Some Studio"}, "stash_ids": []}
class BStash:
    stashdb_endpoint = "sdb"; theporndb_endpoint = "tpdb"  # noqa: E702
    def _graphql(self, q, v): return {"findScene": SCENE}  # noqa: E704
applied_fix = []
library_check.apply_fix = lambda *a, **k: applied_fix.append(a)
imp3 = object.__new__(Importer)
imp3.stash = BStash()
imp3.backup_dir = tempfile.mkdtemp()
it3 = {"scene_id": "9", "provider": "theporndb", "provider_id": "p9", "trusted_reason": "the provider has no fingerprints for this scene"}
imp3._adopt(it3)
saved = json.load(open(it3["backup"]))
check("the scene is saved before the provider data is applied", saved["title"] == "Hand made title" and saved["performers"][0]["name"] == "Somebody" and "import-trusted" in it3["backup"] and len(applied_fix) == 1)
imp4 = object.__new__(Importer); imp4.stash = BStash()  # noqa: E702
it4 = {"scene_id": "9", "provider": "theporndb", "provider_id": "p9"}
imp4._adopt(it4)
check("without a backup folder the import is not blocked", it4["backup"] is None)

print("\nFAILED: " + ", ".join(FAILS) if FAILS else "\nall ok")
sys.exit(1 if FAILS else 0)
