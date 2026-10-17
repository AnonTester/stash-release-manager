"""SABnzbd Archive purge (v0.222.3): download_history_cleanup.purge_archive with a fake client. Run: venv/bin/python scripts/test_sab_archive_purge.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import download_history_cleanup as dh

res = []
def check(name, cond):
    res.append(bool(cond)); print(("ok   " if cond else "FAIL ") + name)

NOW = 1_800_000_000
DAY = 86400
class Fake:
    def __init__(self, ignore=False):
        self.entries = [
            {"nzo_id": "old1", "name": "Old.Release.1", "completed": NOW - 40 * DAY, "category": "xxx"},
            {"nzo_id": "old2", "name": "Old.Release.2", "completed": NOW - 31 * DAY, "category": "XXX"},
            {"nzo_id": "new1", "name": "New.Release", "completed": NOW - 5 * DAY, "category": "xxx"},
            {"nzo_id": "tv1", "name": "Other.Program.Old", "completed": NOW - 90 * DAY, "category": "tv"},
            {"nzo_id": "nodate", "name": "No.Date", "completed": 0, "category": "xxx"},
        ]
        self.deleted, self.ignore = [], ignore
    def get_archive(self, limit=5000): return list(self.entries)
    def delete_archived(self, nzo_id):
        self.deleted.append(nzo_id)
        if not self.ignore: self.entries = [e for e in self.entries if e["nzo_id"] != nzo_id]

row = {"category": "xxx"}
c = Fake()
out = dh.purge_archive(row, 30, dry_run=True, client=c, now=NOW)
check("dry run counts the old entries of the category and removes nothing", out["found"] == 2 and out["dry_run"] and not c.deleted)
out = dh.purge_archive(row, 30, dry_run=False, client=c, now=NOW)
check("real run removes exactly the entries older than 30 days of the client's category", sorted(c.deleted) == ["old1", "old2"] and out["removed"] == 2 and "warning" not in out)
check("a newer entry, another category and an entry without a date stay", {e["nzo_id"] for e in c.entries} == {"new1", "tv1", "nodate"})
c = Fake(); out = dh.purge_archive({}, 30, dry_run=True, client=c, now=NOW)
check("without a category every old entry counts", out["found"] == 3)
c = Fake(ignore=True); out = dh.purge_archive(row, 30, dry_run=False, client=c, now=NOW)
check("SABnzbd ignoring the call is reported, not hidden", "warning" in out)
c = Fake(); out = dh.purge_archive(row, 100, dry_run=False, client=c, now=NOW)
check("a longer age keeps more", out["found"] == 0 and not c.deleted)
sys.exit(0 if all(res) else 1)
