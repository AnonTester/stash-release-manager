"""Tests (v0.197.0): the sanity check for moving/renaming -- other renaming plugins in Stash, library folders, formats, the hook held back, the guard on
Organize library / Import folder, the "moved again" watch. Fake Stash, no network. Run:  venv/bin/python scripts/test_renamer_check.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import renamer_check as rc  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

def plugin(pid, name, desc, hooks, enabled=True):  # noqa: E302
    return {"id": pid, "name": name, "enabled": enabled, "description": desc, "hooks": [{"name": h[0], "description": h[1], "hooks": h[2]} for h in hooks]}
PLUGINS = [
    plugin("renamerOnUpdate", "renamerOnUpdate", "Rename/move filename based on a template.", [("hook_rename", "Rename/move file when you update a scene.", ["Scene.Update.Post"])], enabled=True),
    plugin("filenameParser", "Filename parser", "Parses filename into studio, date, performers and title", [("Prepopulates data based on filename", "", ["Scene.Create.Post"])]),
    plugin("miscTags", "Misc Tags", "Add extra tags for VR based on filename and existing tags.", [("Process Scene", "Adds extra tags", ["Scene.Update.Post"])]),
    plugin("stash-release-manager", "StashReleaseManager", "Tracking, acquisition, sorting and housekeeping ... moves and renames", [("Scene marked Organized", "Moves/renames a scene", ["Scene.Update.Post"])]),
    plugin("performerRenamer", "Performer renamer", "Renames performers", [("x", "", ["Performer.Update.Post"])]),
    plugin("sceneMover", "Scene Mover", "Moves files into folders by tag", [("move", "", ["Scene.Update.Post"])], enabled=False),
]
class FakeStash:
    def __init__(self, plugins, libraries=("/data/scenes", "/data/Movies"), old=False, down=False):  # noqa: E704
        self.plugins, self.libraries, self.old, self.down = plugins, libraries, old, down
    def _graphql(self, query, variables):  # noqa: E301
        if self.down: raise RuntimeError("Stash is down")  # noqa: E701
        if "stashes" in query: return {"configuration": {"general": {"stashes": [{"path": p} for p in self.libraries]}}}  # noqa: E701
        if self.old and " enabled " in query: raise RuntimeError("Cannot query field enabled")  # noqa: E701
        return {"plugins": [{k: v for k, v in p.items() if not (self.old and k == "enabled")} for p in self.plugins]}

found = {r["id"]: r for r in rc.renamer_plugins(FakeStash(PLUGINS))}
check("renamerOnUpdate is found, enabled", found["renamerOnUpdate"]["enabled"] is True)
check("a mover with rename/move wording and a scene hook is found, disabled", "sceneMover" in found and found["sceneMover"]["enabled"] is False)
check("tag / filename plugins are NOT taken for renamers", "filenameParser" not in found and "miscTags" not in found)
check("our own plugin is never listed", "stash-release-manager" not in found)
check("a rename plugin that only hooks performers is not a file renamer", "performerRenamer" not in found)
check("active_renamers: only the enabled ones", [r["id"] for r in rc.active_renamers(FakeStash(PLUGINS))] == ["renamerOnUpdate"])
check("an older Stash without the `enabled` field: assumed enabled (the safe side)", [r["id"] for r in rc.active_renamers(FakeStash(PLUGINS, old=True))] == ["renamerOnUpdate", "sceneMover"])
check("Stash not reachable: no renamers reported, no exception", rc.active_renamers(FakeStash(PLUGINS, down=True)) == [])

SETTINGS = {"organized_hook": "off", "whisparr_root_folder_path": "/data/", "scene_folder_format": "scenes/{Studio Title}/{Scene Title}", "scene_naming_format": "{Scene Title} {Quality Full}",
            "movie_folder_format": "Movies/{Movie Title}< ({Movie Year})>", "movie_naming_format": "{Movie Title}< ({Movie Year})> {Quality Full}"}
def levels(report): return [i["level"] for i in report["issues"]]  # noqa: E704
def texts(report): return " | ".join(i["text"] for i in report["issues"])  # noqa: E704

r = rc.sanity(FakeStash(PLUGINS), lambda k: SETTINGS.get(k, ""))
check("hook off + an enabled renamer: NO warning about renamers at all (v0.202.2), the check is ok", "warning" not in levels(r) and "error" not in levels(r) and "info" not in levels(r)
      and "renam" not in texts(r).lower() and r["ok"])
r_forced = rc.sanity(FakeStash(PLUGINS), lambda k: SETTINGS.get(k, ""), show_renamers=True)
check("hook off, but a manual move was held back (show_renamers): the warning is shown, not an error", "warning" in levels(r_forced) and "error" not in levels(r_forced) and r_forced["ok"])
check("a DISABLED renamer is never mentioned", "Scene Mover" not in texts(r_forced) and "info" not in levels(r_forced))
r = rc.sanity(FakeStash(PLUGINS), lambda k: {**SETTINGS, "organized_hook": "on"}.get(k, ""))
check("hook ON + an enabled renamer: an error that says the hook is held back", "error" in levels(r) and not r["ok"] and "held back" in texts(r))
r = rc.sanity(FakeStash([PLUGINS[1]]), lambda k: SETTINGS.get(k, ""))
r = rc.sanity(FakeStash([PLUGINS[1]]), lambda k: {**SETTINGS, "organized_hook": "on"}.get(k, ""))
check("hook ON, no renamer at all: 'none was found' and everything ok", r["ok"] and "No other enabled plugin" in texts(r))
r = rc.sanity(FakeStash(PLUGINS, down=True), lambda k: SETTINGS.get(k, ""))
check("Stash down: a warning, not a crash", "warning" in levels(r) and "could not be read" in texts(r))

r = rc.sanity(FakeStash([], libraries=("/data/Scenes", "/data/Movies")), lambda k: SETTINGS.get(k, ""))
check("a scene folder 'scenes' while Stash only knows '/data/Scenes' (case!): warned", "scenes folder" in texts(r) and "not inside any Stash library" in texts(r))
check("the movies folder inside a library: ok", "The movies folder (/data/Movies) is inside a Stash library" in texts(r))
r = rc.sanity(FakeStash([], libraries=("/data",)), lambda k: SETTINGS.get(k, ""))
check("a library that is the parent folder covers both", "not inside" not in texts(r))
r = rc.sanity(FakeStash([]), lambda k: {**SETTINGS, "scene_naming_format": "static", "movie_naming_format": "static"}.get(k, ""))
check("formats that give every file the same name are errors", levels(r).count("error") == 2 and not r["ok"])

db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
import history  # noqa: E402
history.init(db)
r = rc.sanity(FakeStash([]), lambda k: SETTINGS.get(k, ""), db)
check("no drift in History: nothing reported", "moved again" not in texts(r).lower())
history.add("warning", "history", "organize", "Moved again by something else: the file this plugin placed was found elsewhere")
r = rc.sanity(FakeStash([]), lambda k: SETTINGS.get(k, ""), db)
check("a 'moved again' entry in History: an error", "error" in levels(r) and "moved again by something else" in texts(r).lower())

# ---- the guards and the watch (Organizer / Importer)
from library_import import Importer  # noqa: E402
from library_organize import Organizer  # noqa: E402
def organizer(cls, stash):  # noqa: E302
    o = object.__new__(cls); o.stash = stash; o.setting = lambda k: SETTINGS.get(k, ""); o.DRIFT_WATCH_SECONDS = 0; return o  # noqa: E702
o = organizer(Organizer, FakeStash(PLUGINS))
g = o.renamer_guard()
check("renamer_guard: an enabled renamer -> error with its id", g and g["renamers"] == ["renamerOnUpdate"] and "back and forth" in g["error"])
check("renamer_guard: ignored when the user confirmed", o.renamer_guard(True) is None)
check("renamer_guard: nothing enabled -> None", organizer(Organizer, FakeStash([PLUGINS[1]])).renamer_guard() is None)
o.queue_guard = lambda force=False: None
check("Organize library apply refuses with the conflict (before any plan)", "renamers" in o.apply("s1", [1]))

imp = organizer(Importer, FakeStash(PLUGINS)); imp.queue_guard = lambda force=False: None
check("Import folder apply refuses with the conflict", "renamers" in imp.apply("/data/Scenes", [1]))

PATHS = {"1": ["/data/Movies/A/A.mkv"], "2": ["/data/elsewhere/A.mkv"]}
imp._scene_paths = lambda sid: PATHS[str(sid)]
NOTES = []
import library_organize  # noqa: E402
library_organize.history.add = lambda *a, **k: NOTES.append((a, k))
imp._check_drift([("1", "/data/Movies/A/A.mkv", "A")])
check("drift watch: the file is where we put it -> nothing", not NOTES)
imp._check_drift([("2", "/data/Movies/A/A.mkv", "A")])
check("drift watch: found elsewhere -> a warning 'Moved again by something else'", NOTES and "Moved again by something else" in NOTES[0][0][3])
check("the watch does not start a thread when switched off", imp._watch_drift([{"scene_id": 1, "new_path": "/x", "title": "t"}]) is None)

# ---- the hook is held back
SETTINGS_NOW = dict(SETTINGS)
imp2 = organizer(Importer, FakeStash(PLUGINS))
imp2._root = lambda: "/data"; imp2.roots = lambda: ["/data/Scenes"]; imp2.queue_guard = lambda force=False: None
scene = {"id": "9", "title": "T", "organized": True, "stash_ids": [], "files": [{"id": "f", "path": "/data/Scenes/x.mkv", "width": 1920, "height": 1080, "duration": 1, "mod_time": "", "fingerprints": []}],
         "groups": [{"scene_index": None, "group": {"id": "1", "name": "Film", "date": "2010-01-01", "scene_count": 1, "urls": ["https://theporndb.net/movies/x"], "studio": None}}], "tags": [], "studio": None, "date": None}
imp2._scene_by_id = lambda sid: scene
imp2.setting = lambda k: {**SETTINGS, "import_sources": '["/data/Scenes"]'}.get(k, "")
imp2.to_local = lambda p: "/nonexistent" + p
APPLIED = []
imp2._apply_items = lambda label, items: (APPLIED.append(items), {"done": items})[1]
import library_import  # noqa: E402
HNOTES = []
library_import.history.add = lambda *a, **k: HNOTES.append((a, k))
r = imp2.organized_scene(9, "on")
check("Organized hook ON with an enabled renamer: NOT moved, History says why, the plan is kept", "blocked" in r and not APPLIED and any("another renamer is enabled" in str(n) for n in HNOTES))
imp2.stash = FakeStash([PLUGINS[1]])
r = imp2.organized_scene(9, "on")
check("the same without a renamer: moved", len(APPLIED) == 1)
APPLIED.clear(); imp2.stash = FakeStash(PLUGINS)
r = imp2.organized_scene(9, "preview")
check("preview mode is not affected (nothing moves anyway)", "preview" in r and not APPLIED)

print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
