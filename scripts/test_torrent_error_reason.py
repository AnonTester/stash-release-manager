"""A torrent in qBittorrent's error state: the reason is read from the client's log and put into the note (v0.253.19)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import qbittorrent_client as qc
import torrent_health as th

fails = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

HASH = "abcdef0123456789abcdef0123456789abcdef01"
MSG = ('File error alert. Torrent: "Some.Torrent". File: "/downloads/incomplete/srm/Some.Torrent/a.mkv.!qB". '
       'Reason: "abcdef file_open (/downloads/incomplete/srm/Some.Torrent/a.mkv.!qB) error: Permission denied"')
check("parse: reason and path", qc.parse_file_error(MSG, "abcdef") == {"reason": "Permission denied", "path": "/downloads/incomplete/srm/Some.Torrent/a.mkv.!qB"})
check("parse: another torrent's alert is ignored", qc.parse_file_error(MSG, "012345") is None)
check("parse: other log lines are ignored", qc.parse_file_error('Failed to move torrent. Reason: "x"', "abcdef") is None)


class FakeResp:
    def __init__(self, data): self.data = data
    def json(self): return self.data

class Client(qc.QbittorrentClient):
    def __init__(self, entries): self.entries = entries
    def _call(self, method, path, **kw):
        assert path == "log/main"
        return FakeResp(self.entries)

tor = {"hash": HASH, "content_path": "/downloads/incomplete/srm/Some.Torrent", "state": "error"}
old = {"message": MSG.replace("Permission denied", "No space left on device")}
found = Client([old, {"message": MSG}, {"message": "unrelated"}]).error_reason(tor)
check("newest matching entry wins, folder = the category folder", found == {"reason": "Permission denied", "path": found["path"], "folder": "/downloads/incomplete/srm"})
check("empty log: nothing", Client([]).error_reason(tor) is None)

t = th.error_text("error", found)
check("permission denied: names the folder and what to do", "/downloads/incomplete/srm" in t and "write access" in t and "resume" in t)
check("no space left", "full" in th.error_text("error", {"reason": "No space left on device", "folder": "/x"}))
check("read-only", "read-only" in th.error_text("error", {"reason": "Read-only file system", "folder": "/x"}))
check("other reason is quoted", "Input/output error" in th.error_text("error", {"reason": "Input/output error", "folder": "/x"}))
check("nothing found: points at the log", "Tools > Log" in th.error_text("error", None))
check("missing files keeps its hint", "recheck" in th.error_text("missingFiles", found))

cfg = {"stall_s": 3600.0, "real_progress": 0.05, "alt_s": 21600.0, "max_gap_s": 4500.0}
v = th.classify({"hash": HASH, "state": "error", "progress": 0}, None, 1000.0, cfg, error_found=found)
check("classify puts the reason into the note", v["state"] == "error" and "permission denied" in v["detail"])

sys.exit(1 if fails else 0)
