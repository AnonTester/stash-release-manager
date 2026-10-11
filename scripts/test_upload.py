"""Test (v0.180.0) of the browser upload of an export file over the WebSocket /transfer/upload, against a RUNNING sidecar (use a throw-away one).
Run:  venv/bin/python scripts/test_upload.py ws://localhost:6964  <path to an export zip>"""
import asyncio
import io
import json
import sys
import zipfile

import websockets

URL, GOOD = sys.argv[1].rstrip("/") + "/transfer/upload", sys.argv[2]
FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)


async def upload(name, data, announce=None, chunk=1024 * 1024, stop_early=False):
    async with websockets.connect(URL, max_size=None) as ws:
        await ws.send(json.dumps({"name": name, "size": len(data) if announce is None else announce}))
        first = json.loads(await ws.recv())
        if "ready" not in first:
            return first
        sent = 0
        for i in range(0, len(data), chunk):
            await ws.send(data[i:i + chunk])
            ack = json.loads(await ws.recv())
            if "error" in ack:
                return ack
            sent = ack["got"]
        await ws.send("end")
        return json.loads(await ws.recv())


async def main():
    good = open(GOOD, "rb").read()
    r = await upload("my export (copy).zip", good)
    check(f"a real export arrives ({len(good) // 1024 // 1024} MB in 1 MB pieces) and is named safely", r.get("ok") and r["name"] == "imports/my_export_copy_.zip")
    r2 = await upload("my export (copy).zip", good)
    check("the same name again does not replace the first (counter)", r2.get("ok") and r2["name"] == "imports/my_export_copy_-2.zip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z: z.writestr("hello.txt", "x")
    r = await upload("other.zip", buf.getvalue())
    check("a zip that is not an export is refused", "error" in r and "ok" not in r)
    r = await upload("notes.txt", b"hello")
    check("a file that is not a .zip is refused", "error" in r)
    r = await upload("../../etc/evil.zip", buf.getvalue())
    check("a path in the name is cut to a safe name (and the content still has to be an export)", "error" in r)
    r = await upload("short.zip", good[:1000], announce=5000)
    check("a file that arrives shorter than announced is refused", "error" in r)
    r = await upload("big.zip", b"x", announce=3 * 1024 ** 3)
    check("more than 2 GB is refused up front", "error" in r)
    print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED")
    sys.exit(1 if FAILS else 0)

asyncio.run(main())
