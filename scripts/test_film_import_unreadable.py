"""Tests of the readability check of the film import (v0.164.0): film_import.unreadable / import_downloaded / finalize against a THROWAWAY database and
throwaway files (a real 1 s test video made with ffmpeg and a junk file of random bytes). Run:  venv/bin/python scripts/test_film_import_unreadable.py"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import film_import  # noqa: E402
import film_repo  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)


root = tempfile.mkdtemp()
good, junk = os.path.join(root, "good.mp4"), os.path.join(root, "junk.wmv")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10", "-pix_fmt", "yuv420p", good], check=True)
with open(junk, "wb") as f:
    f.write(os.urandom(300000))
check("a real video is readable", film_import.unreadable(good) is None)
check("random bytes are not readable (with a reason)", bool(film_import.unreadable(junk)))
check("a missing file is not readable", bool(film_import.unreadable(os.path.join(root, "nope.mp4"))))

db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
cfg = {"local_media_mount": root, "stash_root": "/data", "sab_remote_root": "/dl", "sab_local_mount": root, "recycle_bin_path": ".recycle", "use_hardlinks": False,
       "set_permissions": False, "chmod_folder": "755", "chown_group": ""}
class Stash:  # noqa: E301
    def find_scene_by_path(self, p): return None
    def scan_paths(self, paths): self.scanned = paths
st = Stash()

# import_downloaded: junk is never moved into the library, the movie goes back on the wishlist and the release is rejected
fid, _ = film_repo.add(db, title="Junk Movie", year=2017, tpdb_id="j1", duration_min=60)
film_repo.update(db, fid, status="grabbed", grabbed_title="Junk.Release.nfo", download_client_id=1, download_job_id="x")
film = film_repo.get(db, fid, with_json=True)
out = film_import.import_downloaded(db, st, film, {"storage": "/dl/junk.wmv", "remote_root": "/dl", "local_mount": root}, cfg)
f = film_repo.get(db, fid, with_json=True)
check("unreadable download: status 'unreadable', nothing moved", out["status"] == "unreadable" and not os.path.exists(os.path.join(root, "Movies")) and os.path.exists(junk))
check("movie back on the wishlist, release rejected, reason shown", f["status"] == "wanted" and f["download_job_id"] is None and f["rejected_titles"] == ["Junk.Release.nfo"] and "not a readable video" in f["import_error"])

# finalize: a file in the library that Stash has no scene for, and that is junk -> import_failed (not waiting forever); a good one keeps waiting
os.makedirs(os.path.join(root, "Movies", "A (2017)")); os.makedirs(os.path.join(root, "Movies", "B (2017)"))
os.replace(junk, os.path.join(root, "Movies", "A (2017)", "A.wmv")); os.replace(good, os.path.join(root, "Movies", "B (2017)", "B.mp4"))
a, _ = film_repo.add(db, title="A", year=2017, tpdb_id="a1"); film_repo.update(db, a, status="grabbed", pending_import_path=json.dumps(["/data/Movies/A (2017)/A.wmv"]))
b, _ = film_repo.add(db, title="B", year=2017, tpdb_id="b1"); film_repo.update(db, b, status="grabbed", pending_import_path=json.dumps(["/data/Movies/B (2017)/B.mp4"]))
ra = film_import.finalize(db, st, film_repo.get(db, a, with_json=True), None, None, cfg=cfg)
rb = film_import.finalize(db, st, film_repo.get(db, b, with_json=True), None, None, cfg=cfg)
check("finalize: junk without a scene -> import_failed with the reason", ra["status"] == "import_failed" and film_repo.get(db, a)["status"] == "import_failed" and "not a readable video" in film_repo.get(db, a)["import_error"])
check("finalize: a readable file without a scene yet keeps waiting", rb["status"] == "waiting" and film_repo.get(db, b)["status"] == "grabbed")
rc = film_import.finalize(db, st, film_repo.get(db, b, with_json=True), None, None)
check("finalize without cfg behaves as before (waiting)", rc["status"] == "waiting")

print("\n%s" % ("ALL OK" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
