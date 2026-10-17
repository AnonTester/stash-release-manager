"""Tests (v0.220.0): the encode queue and the encode worker against a real (local) server. Needs ffmpeg. Run: venv/bin/python scripts/test_encode_worker.py"""
import os, shutil, subprocess, sys, tempfile, threading, time, socket
from datetime import date, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
TMP = tempfile.mkdtemp()
os.environ["DATA_DIR"] = TMP
import uvicorn
from fastapi import FastAPI
import encode_queue as eq, file_importer as fi, release_repo, quality_profile_repo, transcode as tc, import_progress
from db import Database
from routers import encode_worker as ew

FAILS = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

DB = Database(os.path.join(TMP, "t.sqlite3"))
def setting(k, v):
    with DB.connect() as c:
        c.execute("INSERT INTO setting (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", (k, v))
old = (date.today() - timedelta(days=12)).isoformat()
with DB.connect() as c:
    pid = quality_profile_repo.create(DB, "Std", [1080, 720], None)
    for sid in ("w1", "w2", "w3", "w5"):
        release_repo.insert_release(DB, c, {"id": sid, "title": "Sunset Fun", "created": old + "T10:00:00Z", "date": old, "studio": {"id": "st", "name": "S"}}, "stashdb", "studio", "st1")
        c.execute("UPDATE release SET status='sent_to_whisparr' WHERE scene_stashdb_id = ?", (sid,))
setting("quality_profile_id", str(pid)); setting("transcode_hw", "off"); setting("transcode_preset", "veryfast"); setting("encode_target", "worker")

# a "download folder" as the sidecar sees it (/downloads) and as the worker sees it (a different path)
SIDE, WORK = os.path.join(TMP, "side"), os.path.join(TMP, "work")
os.makedirs(SIDE); os.symlink(SIDE, WORK)  # same folder under another name = a mount of the same share
def clip(name):
    p = os.path.join(SIDE, name)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=25:duration=3", "-f", "lavfi", "-i", "sine=duration=3",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-c:a", "aac", "-shortest", p], check=True)
    return p

done = threading.Event(); fi.on_encode_done = done.set
app = FastAPI(); ew.init_encode_worker_router(DB); app.include_router(ew.router)
with socket.socket() as s: s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started: time.sleep(0.1)
BASE = f"http://127.0.0.1:{port}"
import json, urllib.request
def call(path, body=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")
STATUS = os.path.join(TMP, "status", "status.json")
WORKER = [sys.executable, os.path.join(os.path.dirname(__file__), "..", "worker", "srm_encode_worker.py"), "--sidecar", BASE, "--once", "--name", "pc", "--status-file", STATUS, "--map", f"{SIDE}={WORK}"]

# 1. the job is queued, and the sidecar's own thread leaves it to the worker
src = clip("w1.mp4")
check("queued: waiting_encode", fi._maybe_reencode(DB, "stashdb", "w1", src) == {"status": "waiting_encode"})
check("... the Activity phase offers 'Encode on the NAS now'", import_progress._state[("stashdb", "w1")].get("can_local") is True)
time.sleep(4.5)
check("the sidecar's own thread does not take it while a worker is wanted", eq.get("stashdb", "w1")["claimed_by"] is None)
st, body = call("/encode-worker/status")
check("status: no worker has asked yet", st == 200 and body["wanted"] and not body["online"])

# 2. the worker takes it, encodes, reports
done.clear()
r = subprocess.run(WORKER, capture_output=True, text=True, timeout=180)
check("worker ran and finished without an error", r.returncode == 0 and "done after" in r.stdout)
stat = json.load(open(STATUS))
check("status file: written, ends 'stopped', remembers the finished job", stat["state"] == "stopped" and stat["name"] == "pc" and stat["last_result"]["ok"] is True
      and stat["last_result"]["file"] == "w1.mp4")
check("the sidecar was told (import run started), the job is gone", done.wait(30) and not fi.encode_active("stashdb", "w1"))
fin = fi._finished_encode("w1", src)
check("the marker points at a 1080p file next to the download", fin and fin["file"].startswith(SIDE) and tc.probe(fin["file"])["height"] == 1080)
check("... and the worker shows as online", call("/encode-worker/status")[1]["online"])

# 3. a worker that cannot see the folder reports a failure
src2 = clip("w2.mp4")
fi._maybe_reencode(DB, "stashdb", "w2", src2)
done.clear()
bad = [a if not a.startswith(SIDE) else f"{SIDE}=/nonexistent" for a in WORKER]
r = subprocess.run(bad, capture_output=True, text=True, timeout=60)
row = release_repo.list_rows_for_scene(DB, "stashdb", "w2")[0]
check("a worker without the folder: the encode is recorded as failed", done.wait(30) and row["status"] == "import_failed" and row["import_error_code"] == "encode_failed")

# 3b. a worker asked to stop (SIGTERM) in the middle of a job: ffmpeg ends, the half file is gone, the status says stopped
src5 = clip("w5.mp4")
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=25:duration=120", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", os.path.join(SIDE, "w5.mp4")], check=True)
fi._maybe_reencode(DB, "stashdb", "w5", src5)
proc = subprocess.Popen([a for a in WORKER if a != "--once"] + ["--interval", "1"], stdout=subprocess.PIPE, text=True)
for _ in range(100):
    time.sleep(0.2)
    try:
        if json.load(open(STATUS)).get("state") == "encoding": break
    except (OSError, ValueError): pass
check("status file: 'encoding' with the file name while a job runs", json.load(open(STATUS)).get("file") == "w5.mp4")
proc.terminate()
try: proc.wait(30)
except subprocess.TimeoutExpired: proc.kill()
st5 = json.load(open(STATUS))
check("SIGTERM during a job: the worker leaves cleanly and says 'stopped'", proc.returncode == 0 and st5["state"] == "stopped")
check("... and no half-written encode is left next to the download", not [f for f in os.listdir(SIDE) if f.startswith("w5") and f.endswith(".srm-encode.mp4")])

# 4. "Encode on the NAS now"
src3 = clip("w3.mp4")
fi._maybe_reencode(DB, "stashdb", "w3", src3)
done.clear()
check("run-local on a job that is not queued is refused", call("/encode-worker/run-local", {"provider": "stashdb", "scene_stashdb_id": "nope"})[0] == 404)
check("run-local accepts the waiting job", call("/encode-worker/run-local", {"provider": "stashdb", "scene_stashdb_id": "w3"})[0] == 200)
check("the sidecar's own thread then encodes it", done.wait(120) and fi._finished_encode("w3", src3) is not None)

# 5. a lost lease: a progress report from a worker that no longer owns the job is refused
eq.add(DB, "stashdb", "w4", src, {"width": 3840, "height": 2160, "duration": 3})
job = eq.claim_remote("pcA")
check("claim marks the job", job and eq.get("stashdb", "w4")["claimed_by"] == "pcA")
eq.get("stashdb", "w4")["beat"] -= eq.LEASE_SECONDS + 5
eq.claim_remote("pcB")  # runs the expiry and hands it to B
check("a silent worker loses the job to the next one", eq.get("stashdb", "w4")["claimed_by"] == "pcB" and call("/encode-worker/progress", {"provider": "stashdb", "stash_id": "w4", "worker": "pcA"})[1] == {"ok": False})

# 6. the check of a worker's result runs in the background: the worker's /done must not wait for it (a NAS needs minutes to decode)
import time as _time
orig_verify = tc.verify_output
tc.verify_output = lambda dst, info: (_time.sleep(2), "boom")[1]
eq.add(DB, "stashdb", "w6", src, {"width": 3840, "height": 2160, "duration": 3})
eq.claim_remote("pcC")
t0 = _time.time()
out = fi.finish_remote_encode("stashdb", "w6", "pcC", True, seconds=5)
check("finish_remote_encode returns at once with 'verifying'", out.get("recorded") == "verifying" and _time.time() - t0 < 1.0)
check("meanwhile nobody else can claim the job", eq.claim_remote("pcD") is None and eq.get("stashdb", "w6")["claimed_by"] == "verify")
for _ in range(60):
    if not eq.active("stashdb", "w6"): break
    _time.sleep(0.2)
check("a failed check ends the job afterwards", not eq.active("stashdb", "w6"))
tc.verify_output = orig_verify

server.should_exit = True
print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
