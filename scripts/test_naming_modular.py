"""Tests (v0.193.0): modular naming -- optional parts <...>, word replacement, tag folders (first match wins), new tokens, the settings checks and the preview
endpoint. No network. Run:  venv/bin/python scripts/test_naming_modular.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import file_naming as fn  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

DETAIL = {"title": "My Scene", "date": "2026-03-14", "studio": {"name": "Some Studio"},
          "performers": [{"performer": {"name": "Jane Doe", "gender": "FEMALE"}}]}
def path(folder, name, tags=None, options=None, detail=DETAIL, quality="WEBDL-1080p"):  # noqa: E302
    return fn.render_path(folder, name, fn.build_tokens(detail, quality, tags=tags, options=options), ".mkv")

# --- existing formats unchanged
check("the default formats give the same result as before", path("scenes/{Studio Title}/{Scene Title} - {Release Date}", "{Scene Title} - {Release Date} {Quality Full}")
      == "scenes/Some Studio/My Scene - 2026-03-14/My Scene - 2026-03-14 WEBDL-1080p.mkv")

# --- optional parts
NAME = "{Scene Title}< - {Release Date}> {Quality Full}"
check("optional part kept when its token has a value", path("s", NAME) == "s/My Scene - 2026-03-14 WEBDL-1080p.mkv")
nodate = {**DETAIL, "date": None}
check("optional part vanishes whole (separator included) without a date", path("s", NAME, detail=nodate) == "s/My Scene WEBDL-1080p.mkv")
check("an optional part with two tokens needs both", path("s", "{Scene Title}< [{Studio Title} {Release Year}]>", detail=nodate) == "s/My Scene.mkv"
      and path("s", "{Scene Title}< [{Studio Title} {Release Year}]>") == "s/My Scene [Some Studio 2026].mkv")
check("a folder segment that is only an optional part is dropped when empty", path("scenes/<{Tag Folder}>/{Studio Title}", "x") == "scenes/Some Studio/x.mkv")
check("an optional part without any token is plain text", path("s", "{Scene Title}< (x)>") == "s/My Scene (x).mkv")

# --- new tokens
check("Release Year / Source / Resolution", path("s", "{Release Year} {Source} {Resolution}") == "s/2026 WEBDL 1080p.mkv")
check("Source/Resolution empty for a quality without a dash", path("s", "<{Source}>{Scene Title}", quality="Unknown") == "s/My Scene.mkv")

# --- replacements
R = [{"find": "Scene", "replace": "Clip", "mode": "text"}]
check("text replacement, case-insensitive", path("s", "{Scene Title}", options={"replacements": [{"find": "scene", "replace": "Clip", "mode": "text"}]}) == "s/My Clip.mkv")
check("word mode only whole words", path("s", "{Scene Title}", detail={**DETAIL, "title": "Scene Scenery"}, options={"replacements": [{"find": "Scene", "replace": "X", "mode": "word"}]}) == "s/X Scenery.mkv")
check("regex mode", path("s", "{Scene Title}", detail={**DETAIL, "title": "Part 12 of 3"}, options={"replacements": [{"find": r"\d+", "replace": "#", "mode": "regex"}]}) == "s/Part # of #.mkv")
check("remove a character (empty replacement)", path("s", "{Studio Title}", detail={**DETAIL, "studio": {"name": "Dad's Place"}}, options={"replacements": [{"find": "'", "replace": "", "mode": "text"}]}) == "s/Dads Place.mkv")
check("rules run in order", path("s", "{Scene Title}", options={"replacements": [{"find": "My", "replace": "Your", "mode": "text"}, {"find": "Your", "replace": "Our", "mode": "text"}]}) == "s/Our Scene.mkv")
check("a broken regex is skipped, not fatal", path("s", "{Scene Title}", options={"replacements": [{"find": "(", "replace": "", "mode": "regex"}]}) == "s/My Scene.mkv")
check("date and quality are never touched by a rule", path("s", "{Release Date} {Quality Full}", options={"replacements": [{"find": "-", "replace": "_", "mode": "text"}]}) == "s/2026-03-14 WEBDL-1080p.mkv")

# --- tag folders
TF = [{"tag": "VR", "folder": "_VR"}, {"tag": "Compilation", "folder": "Compilations"}, {"tag": "Anal"}]
check("a listed tag gives its folder", fn.tag_folder_for(["Anal", "VR"], TF) == "_VR")
check("the list ORDER is the priority, not the order of the scene's tags", fn.tag_folder_for(["Compilation", "VR"], TF) == "_VR" and fn.tag_folder_for(["VR", "Compilation"], TF) == "_VR")
check("without a folder name the tag name is used", fn.tag_folder_for(["anal"], TF) == "Anal")
check("tags that are not listed never compete", fn.tag_folder_for(["Blonde", "POV"], TF) == "")
check("matching is case-insensitive", fn.tag_folder_for(["vr"], TF) == "_VR")
check("no tags / no rules", fn.tag_folder_for(None, TF) == "" and fn.tag_folder_for(["VR"], None) == "")
FOLDER = "scenes/<{Tag Folder}>/{Studio Title}"
check("the {Tag Folder} segment is used for a tagged scene", path(FOLDER, "x", tags=["VR"], options={"tag_folders": TF}) == "scenes/_VR/Some Studio/x.mkv")
check("and left out for an untagged one", path(FOLDER, "x", tags=["POV"], options={"tag_folders": TF}) == "scenes/Some Studio/x.mkv")

# --- settings parsing / checks
check("naming_options reads the stored JSON", fn.naming_options(lambda k: {"tag_folders": '[{"tag": "VR"}]'}.get(k, ""))["tag_folders"] == [{"tag": "VR"}]
      and fn.naming_options(lambda k: "not json")["replacements"] == [])

from fastapi import HTTPException  # noqa: E402
from db import Database  # noqa: E402
from routers import settings as rs  # noqa: E402
import json  # noqa: E402
rs.init_settings_router(Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3")))
def put(key, value):  # noqa: E302
    try:
        rs.set_one(key, {"value": value if isinstance(value, str) else json.dumps(value)}); return True
    except HTTPException:
        return False
check("good replacement rules are stored", put("name_replacements", R) and put("name_replacements", []))
check("a rule without 'find' / with an unknown mode / a bad regex is refused", not put("name_replacements", [{"find": "", "replace": "x"}]) and not put("name_replacements", [{"find": "a", "mode": "magic"}])
      and not put("name_replacements", [{"find": "(", "mode": "regex"}]))
check("good tag folders are stored", put("tag_folders", TF))
check("a tag folder without a tag / with a slash in the folder name is refused", not put("tag_folders", [{"tag": ""}]) and not put("tag_folders", [{"tag": "VR", "folder": "a/b"}]))
check("not a list / not JSON / too many entries are refused", not put("tag_folders", '{"a": 1}') and not put("tag_folders", "nope") and not put("tag_folders", [{"tag": f"t{i}"} for i in range(61)]))

# --- preview
rs.set_one("tag_folders", {"value": json.dumps(TF)})
pv = rs.naming_preview({"folder_format": "scenes/<{Tag Folder}>/{Studio Title}", "filename_format": "{Scene Title}<, {Release Year}> {Quality Full}"})
check("preview: an example without and one with the first tag rule", pv["example"] == "scenes/Example Studio/Example Scene Title, 2026 WEBDL-1080p.mp4"
      and pv["example_with_tag"] == "scenes/_VR/Example Studio/Example Scene Title, 2026 WEBDL-1080p.mp4" and pv["tag"] == "VR")
pv = rs.naming_preview({"tag_folders": []})
check("preview of an unsaved edit (no tag rules): no tag example", pv["example_with_tag"] is None)
print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
