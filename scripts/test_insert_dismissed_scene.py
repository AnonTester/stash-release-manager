"""A scene the user dismissed stays dismissed when another watched entity reports it later (v0.212.0)."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from db import Database
import release_repo

db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
scene = {"id": "s1", "title": "T", "date": "2026-10-01", "created": "2026-10-01T00:00:00Z", "studio": {"id": "st", "name": "S"}}
res = []
def check(name, cond):
    res.append(bool(cond)); print(("ok   " if cond else "FAIL ") + name)
def status(entity):
    return c.execute("SELECT status FROM release WHERE scene_stashdb_id='s1' AND entity_stashdb_id=?", (entity,)).fetchone()[0]

with db.connect() as c:
    release_repo.insert_release(db, c, scene, "stashdb", "performer", "p1")
    check("first sighting is new", status("p1") == "new")
    c.execute("UPDATE release SET status='dismissed'")
    release_repo.insert_release(db, c, scene, "stashdb", "studio", "stu1")
    check("second entity of a dismissed scene -> dismissed", status("stu1") == "dismissed")
    release_repo.insert_release(db, c, {**scene, "id": "s2"}, "stashdb", "performer", "p1")
    check("an unrelated scene stays new", c.execute("SELECT status FROM release WHERE scene_stashdb_id='s2'").fetchone()[0] == "new")
    c.execute("UPDATE release SET status='sent_to_whisparr' WHERE entity_stashdb_id='stu1'")
    release_repo.insert_release(db, c, scene, "stashdb", "studio", "stu2")
    check("not all dismissed -> new", status("stu2") == "new")
    c.execute("UPDATE release SET status='dismissed', muted=1 WHERE scene_stashdb_id='s1'")
    release_repo.insert_release(db, c, scene, "stashdb", "performer", "p9")
    check("only hidden-studio rows dismissed -> performer hit stays new", status("p9") == "new")
print("\nALL OK" if all(res) else "\nFAILED"); sys.exit(0 if all(res) else 1)
