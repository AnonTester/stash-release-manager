"""Remove completed downloads from SABnzbd's history (v0.215.0). Run: venv/bin/python scripts/test_download_history_cleanup.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from db import Database
import release_repo, download_client_repo, download_history_cleanup as dhc
import indexer_search

res = []
def check(name, cond):
    res.append(bool(cond)); print(("ok   " if cond else "FAIL ") + name)

class Sab:
    def __init__(self, slots): self.slots, self.deleted = slots, []
    def get_history(self, search=None, limit=50): return self.slots
    def get_queue(self): return []
    def delete_history(self, nzo, del_files=False): self.deleted.append((nzo, del_files))

db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
cid = download_client_repo.create(db, "sab", "sabnzbd", "usenet", 25, True, "http://x", "k", "", "", "", {})
scene = {"id": "s1", "title": "T", "date": "2026-10-01", "created": "2026-10-01T00:00:00Z"}
with db.connect() as c:
    release_repo.insert_release(db, c, scene, "stashdb", "studio", "e1")
    release_repo.insert_release(db, c, {**scene, "id": "s2"}, "stashdb", "studio", "e1")
    release_repo.insert_release(db, c, {**scene, "id": "s3"}, "stashdb", "studio", "e1")
    c.execute("UPDATE release SET status='imported', grabbed_title='Studio.26.10.01.Title.1080p-GRP' WHERE scene_stashdb_id='s1'")
    c.execute("UPDATE release SET status='imported', download_client_id=?, download_job_id='SABnzbd_nzo_b' WHERE scene_stashdb_id='s2'", (cid,))
    c.execute("UPDATE release SET status='imported', pending_import_path='/x', grabbed_title='Other.Title' WHERE scene_stashdb_id='s3'")  # not verified yet
row = download_client_repo.get_one(db, cid)
slots = [
    {"nzo_id": "a", "name": "Studio_26_10_01_Title_1080p-GRP", "status": "Completed"},   # by name (punctuation differs)
    {"nzo_id": "SABnzbd_nzo_b", "name": "Whatever", "status": "Completed"},              # by job id
    {"nzo_id": "c", "name": "Other.Title", "status": "Completed"},                        # imported but verification pending -> stays
    {"nzo_id": "d", "name": "Someone.Elses.Download", "status": "Completed"},             # not ours -> never touched
    {"nzo_id": "e", "name": "Studio.26.10.01.Title.1080p-GRP", "status": "Failed"},       # failed -> stays
]
sab = Sab(slots)
out = dhc.clean_client(db, row, dry_run=True, client=sab)
check("dry run counts 2 and deletes nothing", out["found"] == 2 and not sab.deleted)
out = dhc.clean_client(db, row, dry_run=False, client=sab)
check("run removes exactly the two of ours, files kept", out["removed"] == 2 and sorted(d[0] for d in sab.deleted) == ["SABnzbd_nzo_b", "a"] and not any(d[1] for d in sab.deleted))
check("ledger knows the name, punctuation-insensitive", dhc.ledger_has(db, "Studio.26.10.01.Title.1080p-GRP") and dhc.ledger_has(db, "Studio 26 10 01 Title 1080p GRP"))
check("ledger finds the title by job id", dhc.ledger_title_for_job(db, "a") == "Studio_26_10_01_Title_1080p-GRP")
check("a name nobody downloaded is not in the ledger", not dhc.ledger_has(db, "Something.Else"))
check("duplicate guard: ledger says no", "downloaded before" in (indexer_search._duplicate_guard(Sab([]), "Studio.26.10.01.Title.1080p-GRP", db) or ""))
check("duplicate guard: unknown release passes", indexer_search._duplicate_guard(Sab([]), "Brand.New.Release", db) is None)
check("option default is on, switchable", dhc.enabled_for({"type": "sabnzbd", "options": {}}) and not dhc.enabled_for({"type": "sabnzbd", "options": {"remove_completed": False}}) and not dhc.enabled_for({"type": "qbittorrent", "options": {}}))
class Boom(Sab):
    def delete_history(self, nzo, del_files=False): raise RuntimeError("down")
out = dhc.clean_client(db, row, dry_run=False, client=Boom(slots))
check("a failing delete is reported, nothing raises", out["removed"] == 0 and len(out["errors"]) == 2)
sys.exit(0 if all(res) else 1)
