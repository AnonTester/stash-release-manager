"""Tests (v0.250.9): the reason shown for a finished download whose file the import cannot find.
Run:  venv/bin/python scripts/test_source_missing_message.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import file_importer as fi  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

err = FileNotFoundError("no video file found under /x")
d = tempfile.mkdtemp()
check("a folder that exists keeps the plain message", fi.source_missing_message(err, d, "/client/path") == str(err))
msg = fi.source_missing_message(err, "/downloads/complete/srm/Release", "/downloads/complete/srm/Release")
check("a missing folder says so and names both paths", "does not exist in the sidecar" in msg and msg.count("/downloads/complete/srm/Release") == 2)
check("it points to the folder pair", "folder pair" in msg)
try:
    fi._find_media_file(d)
    check("an empty folder raises", False)
except FileNotFoundError:
    check("an empty folder raises", True)
print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
