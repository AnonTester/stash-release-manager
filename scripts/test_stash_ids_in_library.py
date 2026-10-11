"""Search page: which hit ids does Stash already have with a file (v0.207.1)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import stash_client

EP = "https://stashdb.org/graphql"

class Fake(stash_client.StashClient):
    def __init__(self): self.calls = []
    def _graphql(self, query, variables):
        ids = variables["scene_filter"]["stash_ids_endpoint"]["stash_ids"]
        self.calls.append(len(ids))
        return {"findScenes": {"scenes": [
            {"id": "1", "files": [{"height": 1080}], "stash_ids": [{"endpoint": EP, "stash_id": "a"}, {"endpoint": "other", "stash_id": "b"}]},
            {"id": "2", "files": [], "stash_ids": [{"endpoint": EP, "stash_id": "c"}]},  # scene without a file: not in the library
        ]}}

f = Fake()
got = f.stash_ids_in_library(EP, ["a", "b", "c", "d", None])
ok = got == {"a": "1"}
print(("ok   " if ok else "FAIL ") + f"file under this endpoint counts, other endpoint / no file do not: {got}")
f2 = Fake(); f2.stash_ids_in_library(EP, [str(i) for i in range(250)])
ok2 = f2.calls == [100, 100, 50]
print(("ok   " if ok2 else "FAIL ") + f"250 ids -> chunks {f2.calls}")
ok3 = Fake().stash_ids_in_library(EP, []) == {}
print(("ok   " if ok3 else "FAIL ") + "no ids -> no query")
print("\nALL OK" if ok and ok2 and ok3 else "\nFAILED")
sys.exit(0 if ok and ok2 and ok3 else 1)
