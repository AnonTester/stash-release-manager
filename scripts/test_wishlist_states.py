"""Search page: release_repo.wishlist_states gives the most advanced state per scene (v0.206.0)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import release_repo

class Conn:
    def __init__(self, rows): self.rows = rows
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def execute(self, q, p): return [{"scene_stashdb_id": a, "status": b} for a, b in self.rows if a in p]
class Db:
    def connect(self): return Conn([("a", "new"), ("b", "sent_to_whisparr"), ("b", "imported"), ("c", "dismissed"), ("d", "backlog"), ("f", "import_failed")])

out = release_repo.wishlist_states(Db(), "stashdb", ["a", "b", "c", "d", "e", "f", None])
ok = out == {"b": "imported", "c": "dismissed", "f": "import_failed"}
print(("ok   " if ok else "FAIL ") + "most advanced state wins; new/backlog/unknown left out: " + str(out))
ok2 = release_repo.wishlist_states(Db(), "stashdb", []) == {}
print(("ok   " if ok2 else "FAIL ") + "no ids -> empty")
print("\nALL OK" if ok and ok2 else "\nFAILED")
sys.exit(0 if ok and ok2 else 1)
