"""'Fix wrong download' does not throw away a file that is a scene we want (wishlist / upgrade list) unless told to (v0.260.14)."""
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
PATH = "/media/scenes/A/wrong.mp4"
def add(sid, status, **kw):
    with db.connect() as conn:
        conn.execute("""INSERT INTO release (provider, scene_stashdb_id, entity_type, entity_stashdb_id, title, scene_created, scene_date, studio_name, status,
                        discovered_at, pending_import_path, import_error_code, grabbed_title) VALUES ('stashdb', ?, 'studio', 'e', ?, '2020-01-01T00:00:00Z', '2020-01-01',
                        'S', ?, datetime('now'), ?, ?, ?)""", (sid, kw.get("title", sid), status, kw.get("path"), kw.get("code"), kw.get("grabbed")))
add("A", "import_failed", path=PATH, code="fingerprint_mismatch", grabbed="Wrong.Release.1080p")
add("B", "sent_to_whisparr", title="Scene B")
add("C", "imported", title="Scene C")

class Stash:
    def find_scene_by_path(self, path):
        return {"id": "77", "files": [{"path": PATH, "fingerprints": [{"type": "oshash", "value": "ab"}]}]}
    def destroy_scene(self, *a, **k): self.destroyed = True
class Whisparr:
    def find_by_stash_id(self, sid): return None
class Provider:
    def __init__(self, matches): self.matches, self.calls = matches, 0
    def find_scenes_by_fingerprints(self, sets):
        self.calls += 1
        return [self.matches]
fi._recycle_existing_target = lambda *a, **k: None

def fix(provider_client, **kw):
    return fi.fix_wrong_download(db, Stash(), Whisparr(), "stashdb", "A", "studio", "e", "/media", "/media", ".recycle-bin",
                                 stashdb_client=provider_client, **kw)
status = lambda: release_repo.list_rows_for_scene(db, "stashdb", "A")[0]["status"]

# 1. nothing stored: the provider is asked; the file is scene B, which is on the wishlist -> refused, nothing changed
p = Provider([{"id": "B", "title": "Scene B"}])
r = fix(p)
check("a wanted scene: refused, answer names it", r.get("wanted_elsewhere", {}).get("id") == "B" and r["wanted_elsewhere"]["list"] == "wishlist")
check("the lookup was made and stored", p.calls == 1 and json.loads(release_repo.list_rows_for_scene(db, "stashdb", "A")[0]["mismatch_found"])["id"] == "B")
check("nothing was changed", status() == "import_failed" and release_repo.list_rows_for_scene(db, "stashdb", "A")[0]["pending_import_path"] == PATH
      and "Wrong.Release.1080p" not in (release_repo.get_review_rejected_titles(db, "stashdb", "A") or []))

# 2. asked again: the stored answer is used (no second lookup)
r = fix(p)
check("second try: refused again from the stored answer", "wanted_elsewhere" in r and p.calls == 1)

# 3. discard anyway
r = fix(p, discard=True)
check("discard=True: done (blocklisted, back on the wishlist)", r.get("status") == "sent_to_whisparr" and status() == "sent_to_whisparr")

# 4. a file that is a scene we do NOT want (C is imported): normal fix
add("A2", "import_failed", path=PATH, code="fingerprint_mismatch", grabbed="Other.Wrong.1080p")
def fix2(pc, **kw):
    return fi.fix_wrong_download(db, Stash(), Whisparr(), "stashdb", "A2", "studio", "e", "/media", "/media", ".recycle-bin", stashdb_client=pc, **kw)
r = fix2(Provider([{"id": "C", "title": "Scene C"}]))
check("a scene we do not want: fixed as before", r.get("status") == "sent_to_whisparr")

# 5. unknown file / failing lookup: fixed as before
add("A3", "import_failed", path=PATH, code="fingerprint_mismatch", grabbed="Third.Wrong.1080p")
class Broken:
    def find_scenes_by_fingerprints(self, s): raise RuntimeError("down")
r = fi.fix_wrong_download(db, Stash(), Whisparr(), "stashdb", "A3", "studio", "e", "/media", "/media", ".recycle-bin", stashdb_client=Broken())
check("a failing lookup does not block the fix", r.get("status") == "sent_to_whisparr")

# 6. VR-check style call (file_path given): no guard
add("A4", "import_failed", path=PATH, code="fingerprint_mismatch", grabbed="Fourth.Wrong.1080p")
release_repo.set_mismatch_found_for_scene(db, "stashdb", "A4", {"id": "B", "title": "Scene B", "list": "wishlist"})
r = fi.fix_wrong_download(db, Stash(), Whisparr(), "stashdb", "A4", "studio", "e", "/media", "/media", ".recycle-bin", file_path=PATH)
check("with a file_path (VR check) the guard does not apply", r.get("status") == "sent_to_whisparr")
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
