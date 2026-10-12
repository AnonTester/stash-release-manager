"""Duration mismatch (v0.209.0): deviation, threshold/direction in the list, the size filter of the re-add search."""
import sqlite3, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import duration_check, upgrades

class Db:
    def __init__(self):
        self.c = sqlite3.connect(":memory:"); self.c.row_factory = sqlite3.Row
        self.c.execute("""CREATE TABLE duration_check (scene_id TEXT PRIMARY KEY, file_path TEXT, file_size INTEGER, scene_title TEXT DEFAULT '',
            provider TEXT, provider_scene_id TEXT, file_sec REAL, provider_sec REAL, status TEXT DEFAULT 'checked', checked_at TEXT DEFAULT '')""")
    def connect(self):
        c = self.c
        class Ctx:
            def __enter__(s): return c
            def __exit__(s, *a): c.commit()
        return Ctx()

res = []
def check(name, cond):
    res.append(bool(cond)); print(("ok   " if cond else "FAIL ") + name)

check("deviation: 40 % shorter", abs(duration_check.deviation(600, 1000) + 0.4) < 1e-9)
check("deviation: missing length -> None", duration_check.deviation(None, 1000) is None and duration_check.deviation(600, 0) is None)

db = Db()
rows = [("1", 600, 1000), ("2", 1020, 1000), ("3", 1049, 1000), ("4", 1051, 1000), ("5", 1500, 1000), ("6", 900, None), ("7", 949, 1000)]
for sid, f, p in rows:
    db.c.execute("INSERT INTO duration_check (scene_id, file_path, provider, provider_scene_id, file_sec, provider_sec) VALUES (?, 'p', 'stashdb', 'x', ?, ?)", (sid, f, p))
ids = lambda **kw: [r["scene_id"] for r in duration_check.list_findings(db, **kw)]
check("5 % threshold: 4 (5.1 %), 5, 1, 7 (5.1 % shorter); 3 (4.9 %) and 2 stay out; no provider length is not listed", sorted(ids(threshold_pct=5)) == ["1", "4", "5", "7"])
check("biggest deviation first (scene 5: 50 %, then scene 1: 40 %)", ids(threshold_pct=5)[:2] == ["5", "1"])
check("direction shorter", sorted(ids(threshold_pct=5, direction="shorter")) == ["1", "7"])
check("direction longer", sorted(ids(threshold_pct=5, direction="longer")) == ["4", "5"])
duration_check.set_status(db, "1", "ignored")
check("ignored rows are left out, unless asked for", "1" not in ids(threshold_pct=5) and "1" in ids(threshold_pct=5, include_ignored=True))

short = {"cur_size": 100, "cur_duration": 300, "provider_duration": 1200}   # file is a quarter of the scene
check("sample: a release 4x the size is plausible", upgrades.duration_size_ok(short, 400))
check("sample: a release the same size as the sample is not", not upgrades.duration_size_ok(short, 105))
check("sample: a 20x release is not (wrong scene / pack)", not upgrades.duration_size_ok(short, 2000))
check("sample: size unknown -> cannot be proven", not upgrades.duration_size_ok(short, None))
long_ = {"cur_size": 400, "cur_duration": 1200, "provider_duration": 600}   # file is twice as long as the scene
check("longer file: a release half the size is plausible", upgrades.duration_size_ok(long_, 200))
check("longer file: a bigger release is not", not upgrades.duration_size_ok(long_, 450))
check("no stored lengths -> no", not upgrades.duration_size_ok({"cur_size": 100}, 400))
check("re-add by hand has no filter (whatever the normal search accepts)", upgrades._better_filter({"reason": "readd"}) is None)
print("\nALL OK" if all(res) else "\nFAILED")
sys.exit(0 if all(res) else 1)
