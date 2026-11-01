"""Tests (v0.249.12): several hand-offs at once fetch Whisparr's studio list ONCE (single flight). No network. Run:
venv/bin/python scripts/test_whisparr_entity_cache.py"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import whisparr_client  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

class Resp:
    def __init__(self, items): self._i = items  # noqa: E704
    def raise_for_status(self): pass  # noqa: E704
    def json(self): return self._i  # noqa: E704

class Session:
    def __init__(self): self.gets, self.timeouts = 0, []  # noqa: E704
    def get(self, url, timeout=None):
        self.gets += 1
        self.timeouts.append(timeout)
        time.sleep(0.4)  # a slow Whisparr
        return Resp([{"foreignId": f"s{i}", "id": i} for i in range(5)])

c = whisparr_client.WhisparrClient.__new__(whisparr_client.WhisparrClient)
c.base, c.timeout, c.session = "http://w/api/v3", 30, Session()
found = []
threads = [threading.Thread(target=lambda i=i: found.append(c._find_by_foreign_id("studio", f"s{i % 5}"))) for i in range(8)]
[t.start() for t in threads]; [t.join() for t in threads]
check("eight hand-offs at once: the list is fetched once", c.session.gets == 1)
check("... and all of them find their studio", len(found) == 8 and all(f is not None for f in found))
check("the long list gets a long timeout", c.session.timeouts[0] >= 120)
check("an unknown studio asks again (it may have been created just now), but only once at a time", c._find_by_foreign_id("studio", "new") is None and c.session.gets == 2)
print("ALL OK" if not FAILS else f"{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
