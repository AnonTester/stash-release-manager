"""Test (v0.186.1): POST /download-clients/check checks the details as typed (nothing saved), per client type. Run:  python3 scripts/test_download_client_check.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import download_client_repo  # noqa: E402
from db import Database  # noqa: E402
from routers import download_clients as dc  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)


calls = []
class FakeSab:  # noqa: E302
    def __init__(self, url, api_key): calls.append(("sab", url, api_key))
    def ping(self):
        if "bad" in calls[-1][1]: raise RuntimeError("SABnzbd said: API Key Incorrect")
class FakeQbt:  # noqa: E302
    def __init__(self, url, username, password): calls.append(("qbt", url, username, password))
    def ping(self):
        if calls[-1][2] == "wrong": raise RuntimeError("qBittorrent login failed")
        return "v5.0.1"
dc.SabnzbdClient, dc.QbittorrentClient = FakeSab, FakeQbt

db = Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
dc.init_download_clients_router(db)
body = dc.DownloadClientCheckBody

check("no client saved at all: a qBittorrent form is still checked with the typed details",
      dc.check_entered(body(type="qbittorrent", url="http://qb:8080", username="u", password="p")) == {"ok": True, "version": "v5.0.1"}
      and calls[-1] == ("qbt", "http://qb:8080", "u", "p"))
check("sabnzbd form uses the typed url and key", dc.check_entered(body(type="sabnzbd", url="http://sab:8080", api_key="k")) == {"ok": True}
      and calls[-1] == ("sab", "http://sab:8080", "k"))
check("a failure comes back as a plain message, HTTP 200", dc.check_entered(body(type="qbittorrent", url="http://qb", username="wrong")) ==
      {"ok": False, "error": "qBittorrent login failed"})
check("sabnzbd failure message is passed on", dc.check_entered(body(type="sabnzbd", url="http://bad", api_key="x"))["error"].startswith("SABnzbd said"))
check("empty URL is refused before any request", dc.check_entered(body(type="sabnzbd", url="  ")) == {"ok": False, "error": "enter the URL first"} and len(calls) == 4)
check("unknown type is reported, not raised", "not implemented" in dc.check_entered(body(type="deluge", url="http://x"))["error"])
check("nothing was saved by the checks", download_client_repo.list_all(db) == [])

# saved client: unchanged behaviour
cid = download_client_repo.create(db, "QB", "qbittorrent", "torrent", 25, True, "http://qb:8080", "", "u", "p", "", {})
check("saved qBittorrent client check still works", dc.check_client(cid) == {"ok": True, "version": "v5.0.1"})
print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
