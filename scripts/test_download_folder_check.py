"""Tests (v0.252.0): the completed-downloads folder check of a download client (can the sidecar see the folder the client reports?).
Run:  venv/bin/python scripts/test_download_folder_check.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import download_folder_check as dfc  # noqa: E402
from routers import download_clients as dc  # noqa: E402
from sabnzbd_client import SabnzbdClient  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

mount = tempfile.mkdtemp()
os.makedirs(os.path.join(mount, "srm"))

r = dfc.check_folder("/downloads/complete", "/downloads/complete", mount)
check("a mapped folder that exists is fine", r["ok"] is True and r["local_path"] == mount)
r = dfc.check_folder("/downloads/complete/srm", "/downloads/complete", mount)
check("a subfolder is translated", r["ok"] is True and r["local_path"] == os.path.join(mount, "srm"))
r = dfc.check_folder("/downloads/complete", "/downloads", mount)
check("a pair that does not fit gives a folder that does not exist", r["ok"] is False and "does not exist" in r["message"] and "/downloads/complete" in r["message"])
r = dfc.check_folder("/data/dl", "/downloads", mount)
check("a client folder outside the entered client folder is reported", r["ok"] is False and "not inside /downloads" in r["message"])
r = dfc.check_folder(mount, "", "")
check("an empty pair uses the path as it is", r["ok"] is True and r["local_path"] == mount)
r = dfc.check_folder("", "/a", "/b")
check("a client without a folder cannot be checked", r["known"] is False and r["ok"] is None)
r = dfc.check_folder("Downloads/complete", "/a", "/b")
check("a relative folder cannot be checked", r["known"] is False)

class FakeQb:
    def completed_folder(self): return "/downloads/complete"
class BrokenQb:
    def completed_folder(self): raise RuntimeError("HTTP 403")
class FakeSab(SabnzbdClient):
    def __init__(self): pass
    def complete_dir(self): return "/downloads/complete"
dc._importer_pair = lambda remote, local: (remote or "/downloads", local or "/downloads")
out = dc._folder(FakeQb(), "/downloads/complete", mount)
check("the router check passes the pair on", out["ok"] is True)
out = dc._folder(FakeQb(), "", "")
check("the importer's fallback pair is used when the client's is empty", out["ok"] is False and "/downloads/complete" in out["message"])
out = dc._folder(FakeSab(), "", mount)
check("SABnzbd is checked through complete_dir()", out["known"] is True)
out = dc._folder(BrokenQb(), "", "")
check("a client that cannot tell never fails the check", out["known"] is False and "HTTP 403" in out["message"])
print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
