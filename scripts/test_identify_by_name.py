"""identify_wrong_file: the release NAME is a clue (v0.260.16): fits this scene -> an unregistered encode; fits another wanted scene -> a hint (never placed)."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import file_importer as fi, release_repo, history  # noqa: E402
from db import Database  # noqa: E402

fails = []
def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)

history._db = None
db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))
history.init(db)
PATH = "/media/x/wrong.avi"
def add(sid, title, date, cast, status, grabbed=None, code=None, path=None):
    with db.connect() as conn:
        conn.execute("""INSERT INTO release (provider, scene_stashdb_id, entity_type, entity_stashdb_id, title, scene_created, scene_date, studio_name, status,
                        discovered_at, cast_names, grabbed_title, import_error_code, pending_import_path) VALUES ('stashdb', ?, 'studio', 'e', ?, '2026-11-01T00:00:00Z', ?,
                        'Studio', ?, datetime('now'), ?, ?, ?, ?)""", (sid, title, date, status, cast, grabbed, code, path))
add("X", "Double Fun", "2026-11-15", "Jane Roe", "import_failed", grabbed="Studio.26.11.15.Jane.Roe.Double.Fun.XXX.XviD-GRP", code="fingerprint_mismatch", path=PATH)
add("Y", "Other Thing", "2026-11-14", "Mia Poe", "sent_to_whisparr")
add("Z", "Third", "2026-09-01", "Mia Poe", "import_failed", grabbed="Studio.26.11.14.Mia.Poe.Other.Thing.XXX.1080p-GRP", code="fingerprint_mismatch", path=PATH)

class Stash:
    def find_scene_by_path(self, path):
        return {"id": "1", "files": [{"path": PATH, "fingerprints": [{"type": "oshash", "value": "h1"}]}]}
class Provider:
    def find_scenes_by_fingerprints(self, sets): return [[]]

r = fi.identify_wrong_file(db, Stash(), Provider(), "stashdb", "X", PATH)
v = json.loads(release_repo.list_rows_for_scene(db, "stashdb", "X")[0]["mismatch_found"])
check("name fits this scene, nothing else known: empty check with name_fits true", r is None and v["id"] is None and v["name_fits"] is True)
r = fi.identify_wrong_file(db, Stash(), Provider(), "stashdb", "Z", PATH)
check("name does not fit Z but fits wanted scene Y: a hint with source 'name'", r and r["id"] == "Y" and r["source"] == "name" and r["list"] == "wishlist")
check("a name-only clue is never placed automatically",
      "only suggests" in (fi.place_file_for_other_scene(db, Stash(), None, "stashdb", "Z", get_provider_client=lambda p: Provider()).get("error") or ""))
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
