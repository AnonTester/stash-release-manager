"""The saved cover of a deleted group is also served as a data: URL (v0.255.1), so the plugin never loads it from the sidecar's own address."""
import base64
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from fastapi import HTTPException
from routers import films

fails = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

tmp = tempfile.mkdtemp()
os.makedirs(os.path.join(tmp, films._SEED_IMAGES))
png = b"\x89PNG\r\n\x1a\n" + b"x" * 20
with open(os.path.join(tmp, films._SEED_IMAGES, "g1_front.img"), "wb") as fh:
    fh.write(png)
films._data_dir = tmp
films.film_repo = type("R", (), {"get": staticmethod(lambda db, i: {"source_ref": "deleted-group:g1"} if i == 1 else {"source_ref": "deleted-group:none"} if i == 2 else {"source_ref": ""})})

out = films.seed_cover_data(1)
check("data URL with the right type and bytes", out["data"] == "data:image/png;base64," + base64.b64encode(png).decode())
for fid, name in ((2, "missing file"), (3, "not a deleted group")):
    try:
        films.seed_cover_data(fid); ok = False
    except HTTPException as e:
        ok = e.status_code == 404
    check(f"404 for {name}", ok)
check("the plain image endpoint still answers", films.seed_cover(1).media_type == "image/png")
sys.exit(1 if fails else 0)
