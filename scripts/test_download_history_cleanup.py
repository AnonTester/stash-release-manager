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

# --- v0.215.1: movies whose grabbed_title ends in ".par2", and downloads this program blocklisted / rejected
db2 = Database(os.path.join(tempfile.mkdtemp(), "t2.sqlite3"))
cid2 = download_client_repo.create(db2, "sab", "sabnzbd", "usenet", 25, True, "http://x", "k", "", "", "", {})
import json
with db2.connect() as c:
    c.execute("INSERT INTO film_want (title, status, grabbed_title, source, source_ref) VALUES ('Stoya', 'imported', 'dp-Stoya .web.whore.1080p.x264 - 001167 dp-stoya.web.whore.1080p.x264.par2', 't', 'r1')")
    c.execute("INSERT INTO film_want (title, status, grabbed_title, source, source_ref) VALUES ('Open', 'wanted', 'Open.Film.1080p.WEB-GRP.par2', 't', 'r2')")
    release_repo.insert_release(db2, c, scene, "stashdb", "studio", "e1")
    c.execute("UPDATE release SET review_rejected_titles = ?", (json.dumps(["Anna.Placeholder.Title.Here.1080p", "exact:SomeStudio.26.10.03.Tomi.XXX.MP4-GRP", "short"]),))
row2 = download_client_repo.get_one(db2, cid2)
slots2 = [
    {"nzo_id": "1", "name": "dp-Stoya_.web.whore.1080p.x264_-_001167_dp-stoya.web.whore.1080p.x264", "status": "Completed"},  # imported movie, ".par2" title
    {"nzo_id": "2", "name": "Open.Film.1080p.WEB-GRP", "status": "Completed"},                                              # movie not imported -> stays
    {"nzo_id": "3", "name": "Anna.Placeholder.Title.Here.1080P.x265", "status": "Completed"},                                    # other encode of a blocklisted name
    {"nzo_id": "4", "name": "SomeStudio.26.10.03.Tomi.XXX.MP4-GRP", "status": "Completed"},                            # exact reject
    {"nzo_id": "5", "name": "SomeStudio.26.10.03.Tomi.XXX.MP4-GRP.1080p.HEVC", "status": "Completed"},                  # a different encode of an EXACT reject: still caught by prefix (same release family)
    {"nzo_id": "6", "name": "short", "status": "Completed"},                                                                 # too short to match by prefix
    {"nzo_id": "7", "name": "Anna.Placeholder.Title.Here.1080p", "status": "Failed"},                                            # failed stays
    {"nzo_id": "8", "name": "Something.Unrelated.2026", "status": "Completed"},
    {"nzo_id": "9", "name": "Anna.Placeholder.Title.Here.1080p", "status": "Completed", "script_line": 'srw_notify: sidecar answered {"ours":false}'},  # a manual download of someone else, same name
]
found = {c["nzo_id"]: c["matched_by"] for c in dhc.find_candidates(db2, row2, Sab(slots2))}
check("movie with a .par2 grabbed_title is matched when imported", found.get("1") == "name")
check("movie that is not imported stays", "2" not in found)
check("other encode of a blocklisted release is removed", found.get("3") == "blocked")
check("exact-rejected release is removed", found.get("4") == "blocked")
check("too-short names are not matched by their start; failed and unrelated stay", "6" not in found and "7" not in found and "8" not in found)
check("a blocked name downloaded by someone else (script says ours:false) is left alone", "9" not in found)

# --- v0.218.1: a rejected release is blocked for ITS scene only, not for every scene
sab2 = Sab(slots2)
out2 = dhc.clean_client(db2, row2, dry_run=False, client=sab2)
with db2.connect() as c:
    outcomes = {r["title"]: r["outcome"] for r in c.execute("SELECT title, outcome FROM downloaded_release")}
check("ledger: imported movie -> 'imported', rejected names -> 'rejected'",
      outcomes.get("dp-Stoya_.web.whore.1080p.x264_-_001167_dp-stoya.web.whore.1080p.x264") == "imported"
      and outcomes.get("Anna.Placeholder.Title.Here.1080P.x265") == "rejected")
check("a rejected name does NOT block other scenes (ledger)", not dhc.ledger_has(db2, "Anna.Placeholder.Title.Here.1080P.x265"))
check("an imported name does block (ledger)", dhc.ledger_has(db2, "dp-Stoya .web.whore.1080p.x264 - 001167 dp-stoya.web.whore.1080p.x264"))
check("guard: a rejected name still in the client's history is no reason to refuse it for another scene",
      indexer_search._duplicate_guard(Sab([{"name": "Anna.Placeholder.Title.Here.1080P.x265", "status": "Completed"}]), "Anna.Placeholder.Title.Here.1080P.x265", db2) is None)
check("guard: a foreign name in the history is still a duplicate",
      "history" in (indexer_search._duplicate_guard(Sab([{"name": "Some.Other.Release.2026.1080p", "status": "Completed"}]), "Some.Other.Release.2026.1080p", db2) or ""))
STOYA = "dp-Stoya_.web.whore.1080p.x264_-_001167_dp-stoya.web.whore.1080p.x264"
check("guard: an imported movie's name in the history (or the ledger) is still a duplicate",
      bool(indexer_search._duplicate_guard(Sab([{"name": STOYA, "status": "Completed"}]), STOYA, db2)))
# migration of an old ledger (no outcome column)
db3 = Database(os.path.join(tempfile.mkdtemp(), "t3.sqlite3"))
with db3.connect() as c:
    c.execute("DROP TABLE downloaded_release")
    c.execute("CREATE TABLE downloaded_release (name_norm TEXT PRIMARY KEY, title TEXT NOT NULL, nzo_id TEXT, client_id INTEGER, removed_at TEXT NOT NULL DEFAULT (datetime('now')))")
    release_repo.insert_release(db3, c, scene, "stashdb", "studio", "e1")
    c.execute("UPDATE release SET status='imported', grabbed_title='Studio.26.10.01.Title.1080p-GRP' WHERE scene_stashdb_id='s1'")
    c.execute("UPDATE release SET review_rejected_titles = ? WHERE scene_stashdb_id='s1'", (json.dumps(["Wrong.Release.For.Another.Scene.1080p-X"]),))
    for t in ("Studio.26.10.01.Title.1080p-GRP", "Wrong.Release.For.Another.Scene.1080p-X", "Unknown.Old.Entry.1080p"):
        c.execute("INSERT INTO downloaded_release (name_norm, title) VALUES (?, ?)", (dhc.norm(t), t))
    db3._migrate_downloaded_release(c)
    got = {r["title"]: r["outcome"] for r in c.execute("SELECT title, outcome FROM downloaded_release")}
check("migration: imported stays 'imported', a blocklist-only name becomes 'rejected', unknown stays 'imported'",
      got == {"Studio.26.10.01.Title.1080p-GRP": "imported", "Wrong.Release.For.Another.Scene.1080p-X": "rejected", "Unknown.Old.Entry.1080p": "imported"})
# --- v0.221.7: a blocklisted release picked by hand again: its entry stays while the import is open
with db2.connect() as c:
    c.execute("UPDATE release SET status='sent_to_whisparr', download_client_id=?, download_job_id='manual-1'", (cid2,))
slots3 = [{"nzo_id": "manual-1", "name": "Anna.Placeholder.Title.Here.1080P.x265", "status": "Completed"},   # blocked name, but the import of this job is open
          {"nzo_id": "other-2", "name": "SomeStudio.26.10.03.Tomi.XXX.MP4-GRP", "status": "Completed"}]  # blocked name, nobody waits for it
found3 = {c["nzo_id"] for c in dhc.find_candidates(db2, row2, Sab(slots3))}
check("an open import keeps its history entry although the name is blocked", "manual-1" not in found3 and "other-2" in found3)

# --- v0.221.9: the removal is real (SABnzbd would archive it, and its duplicate detection still reads the archive)
import sabnzbd_client
sent = {}
class _Resp:
    ok = True
    def json(self): return {}
sabnzbd_client.requests.get = lambda url, params=None, timeout=None: (sent.update(params or {}), _Resp())[1]
sabnzbd_client.SabnzbdClient("http://x", "k").delete_history("abc")
check("delete_history asks SABnzbd to remove for real (archive=0), files kept", sent.get("archive") == 0 and sent.get("name") == "delete" and sent.get("del_files") == 0)

sys.exit(0 if all(res) else 1)
