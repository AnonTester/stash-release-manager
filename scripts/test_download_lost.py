"""A download job the client no longer knows (v0.221.8): the scene leaves "waiting for import" for Stuck. Run: venv/bin/python scripts/test_download_lost.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from db import Database
import release_repo, download_client_repo, download_jobs, download_sources

res = []
def check(name, cond):
    res.append(bool(cond)); print(("ok   " if cond else "FAIL ") + name)

class FakeSab:
    history, queue = {}, []
    def __init__(self, url, key): pass
    def find_history_by_nzo_id(self, nzo): return FakeSab.history.get(nzo)
    def get_queue(self): return FakeSab.queue
download_jobs.SabnzbdClient = FakeSab

row = {"type": "sabnzbd", "url": "http://x", "api_key": "k"}
check("in the queue -> downloading", (setattr(FakeSab, "queue", [{"nzo_id": "j1"}]), download_jobs.job_state(row, "j1")["state"])[1] == "downloading")
FakeSab.queue = []
check("in neither queue nor history -> unknown", download_jobs.job_state(row, "j1")["state"] == "unknown")
FakeSab.history = {"j1": {"status": "Completed", "storage": "/d/x", "name": "x"}}
check("in the history -> completed", download_jobs.job_state(row, "j1")["state"] == "completed")

old, fresh = "2000-01-01 00:00:00", download_jobs.datetime.now(download_jobs.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
check("unknown and old -> lost", download_jobs.is_lost({"state": "unknown"}, old))
check("unknown but just grabbed -> not lost yet", not download_jobs.is_lost({"state": "unknown"}, fresh))
check("a known job is never lost", not download_jobs.is_lost({"state": "downloading"}, old) and not download_jobs.is_lost({"state": "unknown"}, None))

# the whole path: the scene goes from 'waiting' to Stuck (no job, a result text), nothing is blocklisted
db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
cid = download_client_repo.create(db, "sab", "sabnzbd", "usenet", 25, True, "http://x", "k", "", "", "", {})
scene = {"id": "s1", "title": "Rosie", "date": "2026-09-11", "created": "2026-09-11T00:00:00Z"}
with db.connect() as c:
    release_repo.insert_release(db, c, scene, "stashdb", "studio", "e1")
    c.execute("UPDATE release SET status='sent_to_whisparr', download_client_id=?, download_job_id='gone', grabbed_title='X.Rosie.2160p', grabbed_at=?, last_search_result='grabbed'", (cid, old))
FakeSab.history, FakeSab.queue = {}, []
items = download_sources.SabnzbdSource(db).list_completed_downloads()
r = release_repo.list_rows_for_scene(db, "stashdb", "s1")[0]
check("nothing to import; the job is dropped, the reason is shown", items == [] and r["download_job_id"] is None and r["last_search_result"].startswith("download lost"))
check("the release is not blocklisted", not release_repo.get_review_rejected_titles(db, "stashdb", "s1"))
print("\nALL OK" if all(res) else "\nFAILED"); sys.exit(0 if all(res) else 1)
