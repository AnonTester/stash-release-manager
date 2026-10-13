"""Compare groups (v0.213.0): title matching of film folders against empty groups."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import group_compare as gc
from db import Database

res = []
def check(name, cond):
    res.append(bool(cond)); print(("ok   " if cond else "FAIL ") + name)
def hit(a, b): return gc.compare(a, b) is not None

check("Vol. in the group name", hit("Tonight's Girlfriend 19", "Tonight's Girlfriend Vol. 19"))
check(", The at the end", hit("The Perfectionist", "Perfectionist, The"))
check("Lesson / colon", hit("Russian Institute 20 The New School Girl", "Russian Institute: Lesson 20 - The New School Girl"))
check("accents and umlauts", hit("In der Wichskabine uberrascht", "In der Wichskabine Überrascht"))
check("tag in brackets", hit("Host's POV 13 (WEB)", "Host's POV 13"))
check("# number", hit("Home Made Couples 2", "Home Made Couples #2"))
check("roman numeral", hit("Miami Spice II", "Miami Spice 2"))
check("other number -> no", not hit("Secretaries 2", "Secretaries 3"))
check("other number -> no (#17 / 19)", not hit("Anales Casting 19", "Anales Casting #17"))
check("extra number -> no", not hit("Horny 18 #1", "Horny 18"))
check("no number vs 1 -> yes, with a note", gc.compare("Gaper Maker 1", "Gaper Maker") and gc.compare("Gaper Maker 1", "Gaper Maker")[1])
check("different films -> no", not hit("After Hours", "Allure"))
check("year is no condition", hit("Anal Erotica 1 2013", "Anal Erotica 1"))

db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
folders = [{"root": "/data/Movies", "folder": "Wife Switch 12 (2011)", "title": "Wife Switch 12", "year": "2011", "minutes": 90, "videos": [{"scene_id": "1"}]},
           {"root": "/data/movies", "folder": "Allure (1996)", "title": "Allure", "year": "1996", "minutes": 80, "videos": [{"scene_id": "2"}]}]
groups = [{"id": "5", "name": "Wife Switch Vol. 12", "date": "2013-04-15", "scenes": 0}, {"id": "6", "name": "Unrelated", "date": None, "scenes": 0}]
out = gc.build(db, folders, groups)
check("one pair, the other folder has none", [p["title"] for p in out["pairs"]] == ["Wife Switch 12"])
check("year difference is noted", out["pairs"][0]["candidates"][0]["year_note"].startswith("year 2011"))
check("unmatched group listed", [g["id"] for g in out["unmatched_groups"]] == ["6"])
gc.set_ignored(db, "/data/Movies/Wife Switch 12 (2011)", "5")
out = gc.build(db, folders, groups)
check("'Not this' hides the pair and counts it", out["pairs"] == [] and out["ignored"] == 1 and len(out["unmatched_groups"]) == 2)
gc.set_ignored(db, "/data/Movies/Wife Switch 12 (2011)", "5", False)
check("undo brings it back", len(gc.build(db, folders, groups)["pairs"]) == 1)
print("\nALL OK" if all(res) else "\nFAILED"); sys.exit(0 if all(res) else 1)
