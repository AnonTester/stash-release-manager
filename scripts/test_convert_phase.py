"""Tests (v0.249.7): the steps of a finished conversion are told to the page (name, Stash scan percent, seconds in the step). No network.
Run:  venv/bin/python scripts/test_convert_phase.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import convert  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

class Stash:
    def wait_for_job(self, j, on_progress=None):
        for pr in (0.25, 0.5):
            on_progress({"progress": pr, "status": "RUNNING"})
            self.seen = dict(convert._phase[7])
convert.set_phase(7, "x")
convert._wait_scan(Stash(), "job", 7, "Stash scans the converted file")
check("the Stash scan's progress becomes a percent while it runs", True)
st = Stash(); convert._wait_scan(st, "job", 7, "Stash scans the converted file")
check("... 50 % at the last report", st.seen["percent"] == 50 and st.seen["step"] == "Stash scans the converted file")
check("... and 100 % when it is over", convert._phase[7]["percent"] == 100)
since = convert._phase[7]["since"]
convert.set_phase(7, "joining the converted file to the scene")
check("a new step restarts its own timer, the whole one keeps running", convert._phase[7]["since"] == since and convert._phase[7]["percent"] is None)
convert.clear_phase(7)
check("cleared at the end", 7 not in convert._phase)
print("ALL OK" if not FAILS else f"{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
