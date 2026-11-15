"""Review: Identify Scene -- adopt with `organize` renames / moves the file right away (v0.260.10); without it nothing is organized."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import file_importer, routers.compare as rc  # noqa: E402

fails = []
def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)

class Fake:
    def __init__(self, ok=True): self.ok = ok
    def adopt(self, pair_id, scene_id, provider, provider_id):
        return {"ok": True, "scene": {"title": "T"}} if self.ok else {"error": "claimed by another scene"}
calls = []
file_importer.organize_scene_hook = lambda sid: calls.append(sid) or {"done": [{"path": "a"}], "failed": []}
body = {"scene_id": 5, "provider": "stashdb", "provider_id": "x"}

rc._compare = lambda: Fake()
r = rc.pair_adopt(0, dict(body, organize=True))
check("organize: the hook runs for that scene", calls == ["5"] and r["organized"]["done"])
calls.clear()
r = rc.pair_adopt(0, dict(body))
check("no organize flag: nothing is moved", calls == [] and "organized" not in r)
rc._compare = lambda: Fake(ok=False)
r = rc.pair_adopt(0, dict(body, organize=True))
check("a refused adopt does not organize", calls == [] and r.get("error"))
rc._compare = lambda: Fake()
def boom(sid): raise RuntimeError("disk")
file_importer.organize_scene_hook = boom
r = rc.pair_adopt(0, dict(body, organize=True))
check("a failing organizer is reported, the adopt stands", r["ok"] and r["organized"]["failed"][0]["reason"].startswith("RuntimeError"))
print("FAILED: " + ", ".join(fails) if fails else "ALL OK")
sys.exit(1 if fails else 0)
