"""Tests of the readability check of the scene import (v0.165.1): file_importer._reject_unreadable against a THROWAWAY database and throwaway files
(a real 1 s test video made with ffmpeg and a junk file of random bytes). Run:  venv/bin/python scripts/test_scene_import_unreadable.py"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import file_importer  # noqa: E402
import release_repo  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)


root = tempfile.mkdtemp()
dl = os.path.join(root, "dl", "job")
os.makedirs(dl)
good, junk = os.path.join(dl, "good.mp4"), os.path.join(dl, "junk.wmv")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10", "-pix_fmt", "yuv420p", good], check=True)
with open(junk, "wb") as f:
    f.write(os.urandom(300000))

db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
with db.connect() as conn:
    release_repo.insert_release(db, conn, {"id": "s1", "title": "T", "created": "2026-01-01"}, "stashdb", "studio", "st1", status="sent_to_whisparr")
release_repo.set_grabbed_for_scene(db, "stashdb", "s1", "Some.Release.XXX")
release_repo.set_download_job_for_scene(db, "stashdb", "s1", 1, "nzo1")

args = (db, "stashdb", "s1")
check("a readable video passes (None)", file_importer._reject_unreadable(*args, good, dl, root, ".recycle") is None)
check("the readable file is untouched", os.path.exists(good))
out = file_importer._reject_unreadable(*args, junk, dl, root, ".recycle", keep_source=True)
check("junk is rejected as failed with the reason", out and out["status"] == "failed" and "not a readable video" in out["error"])
check("keep_source (torrent): file stays", os.path.exists(junk))
row = release_repo.list_rows_for_scene(db, "stashdb", "s1")[0]
check("error code unreadable recorded", row["import_error_code"] == "unreadable")
check("release name blocklisted", "Some.Release.XXX" in (row["review_rejected_titles"] or ""))
check("download job cleared", row["download_job_id"] is None)
out = file_importer._reject_unreadable(*args, junk, dl, root, ".recycle")
check("usenet junk: rejected", out and out["status"] == "failed")
check("usenet junk: gone from the download folder", not os.path.exists(junk))
check("usenet junk: in the recycle bin", any("junk.wmv" in f for _r, _d, fs in os.walk(os.path.join(root, ".recycle")) for f in fs))
print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
