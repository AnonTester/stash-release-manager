"""Test (v0.250.7): a release posted long before the scene's release date is another scene (studios that call every episode the same).
Run:  venv/bin/python scripts/test_predates_scene.py"""
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import indexer_search as ix  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

TODAY = datetime(2026, 11, 7, tzinfo=timezone.utc)
D = "2026-11-03"
check("three-year-old post of a 4-day-old scene predates", ix.predates_scene(D, {"age": 1024}, TODAY))
check("fresh post is fine", not ix.predates_scene(D, {"age": 3}, TODAY))
check("post a few days older than the date is fine (slack)", not ix.predates_scene(D, {"age": 12}, TODAY))
check("no age -> no proof", not ix.predates_scene(D, {"age": None}, TODAY) and not ix.predates_scene(D, {}, TODAY))
check("no full date -> no proof", not ix.predates_scene("2026", {"age": 5000}, TODAY) and not ix.predates_scene(None, {"age": 5000}, TODAY))
check("old scene, old post fine", not ix.predates_scene("2022-06-21", {"age": 1595}, TODAY))

def rel(title, age):
    return {"title": title, "age": age, "indexer": "X", "size": 3_000_000_000, "downloadUrl": "u" + title,
            "quality": {"quality": {"resolution": 1080, "source": "web"}}}
releases = [rel("Beth...Placeholder.Title.Here.1080p", 1024), rel("Alexa..Placeholder.Title.Here.1080p", 842)]
args = ([1080], {}, "placeholder title", D, ["Luna Yuki", "Performer Two"])
check("without the age rule the old fakes were grabbed", ix._candidate_plausible("placeholder title", D, ["Luna Yuki"], releases[0]["title"]))
check("_select_best takes none of them", ix._select_best(releases, *args, require_vr=False, rejected_titles=[]) is None)
verdicts = [e["verdict"] for e in ix._explain_candidates(releases, [1080], "placeholder title", D, ["Luna Yuki"], False, [], None, None)]
check("log says too_old", verdicts == ["too_old", "too_old"])
fresh = rel("Luna.Placeholder.Title.Here.1080p", 2)
check("a fresh one is still taken", ix._select_best(releases + [fresh], *args, require_vr=False, rejected_titles=[]) is fresh)
sys.exit(1 if FAILS else 0)
