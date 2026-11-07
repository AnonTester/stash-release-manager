"""Test (v0.250.6): a moved file Stash never scanned (scan job lost in a Stash restart) gets ONE new scan job from the retry run.
Run:  venv/bin/python scripts/test_rescan_lost_scan.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import file_importer as fi  # noqa: E402
import release_repo  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

db = Database(os.path.join(os.environ["DATA_DIR"], "t.sqlite3"))
PATHS = {"a": "/data/scenes/x/a.mp4", "b": "/data/scenes/x/b.mp4"}
for sid, path in PATHS.items():
    with db.connect() as conn:
        release_repo.insert_release(db, conn, {"id": sid, "title": sid, "date": "2026-01-01", "created": "2026-01-01", "performers": []},
                                    "stashdb", "studio", "e1", status="sent_to_whisparr")
    release_repo.set_pending_import_path_for_scene(db, "stashdb", sid, path)

class Stash:
    scans = []
    known = {PATHS["b"]}  # b is in Stash (verification just not ready), a is not
    def find_scene_by_path(self, path): return {"id": "1"} if path in self.known else None
    def scan_paths(self, paths): self.scans.append(list(paths)); return "77"
fi._provider_client = lambda provider, stashdb, getter: stashdb
fi._finalize_verification = lambda *a: None  # still not verifiable

st = Stash()
fi.retry_pending_verifications(db, st, None)
check("one scan job, only for the unknown file", st.scans == [[PATHS["a"]]])
fi.retry_pending_verifications(db, st, None)
check("not stacked again within 20 minutes", len(st.scans) == 1)
fi._rescan_last[PATHS["a"]] -= 21 * 60
fi.retry_pending_verifications(db, st, None)
check("scanned again after 20 minutes", len(st.scans) == 2)
check("pending path kept", len(release_repo.list_pending_sidecar_verifications(db)) == 2)
sys.exit(1 if FAILS else 0)
