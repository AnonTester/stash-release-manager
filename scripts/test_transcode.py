"""Tests (v0.218.0): the last-resort 2160p/SD rule and the re-encode to 1080p. Needs ffmpeg/ffprobe. Run: venv/bin/python scripts/test_transcode.py"""
import os, subprocess, sys, tempfile
from datetime import date, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
TMP = tempfile.mkdtemp()
os.environ["DATA_DIR"] = TMP
import transcode as tc
import indexer_search as ix
import file_importer as fi
import release_repo, quality_profile_repo
from db import Database

FAILS = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)

check("target size: 2160p -> 1920x1080", tc.target_size(3840, 2160) == (1920, 1080))
check("target size: ultrawide keeps its shape, even numbers", tc.target_size(3840, 1600) == (1920, 800))
check("target size: nothing is scaled up", tc.target_size(1280, 720) == (1280, 720))

src = os.path.join(TMP, "src.mp4")
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=25:duration=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-c:a", "aac", "-shortest", src], check=True)
info = tc.probe(src)
check("probe reads size, length and audio", info["width"] == 3840 and info["height"] == 2160 and abs(info["duration"] - 3) < 0.2 and info["acodec"] == "aac" and not info["hdr"])
seen = []
dst = os.path.join(TMP, "out.mp4")
res = tc.transcode(src, dst, quality=26, hw_mode="off", progress=lambda *a: seen.append(a))
out = tc.probe(dst)
check("software encode: 1080p HEVC, same length, audio kept", res["ok"] and not res["hw"] and out["height"] == 1080 and out["vcodec"] == "hevc" and out["acodec"] == "aac" and abs(out["duration"] - info["duration"]) < 0.5)
check("progress was reported", len(seen) >= 1 and all(0 <= s[0] <= 99 for s in seen))
check("verify_output accepts it and rejects a wrong length", tc.verify_output(dst, info) is None and tc.verify_output(dst, {**info, "duration": 30.0}))
res = tc.transcode(os.path.join(TMP, "missing.mp4"), os.path.join(TMP, "x.mp4"), hw_mode="off")
check("an unreadable source fails cleanly, nothing left behind", not res["ok"] and not os.path.exists(os.path.join(TMP, "x.mp4")))
check("audio the mp4 cannot carry -> mkv", tc.output_extension({"acodec": "dts"}) == ".mkv" and tc.output_extension({"acodec": "aac"}) == ".mp4")
if tc.hw_available():
    res = tc.transcode(src, os.path.join(TMP, "hw.mp4"), quality=26, hw_mode="auto")
    check("hardware encode (VAAPI here): 1080p, or a clean fall back to the CPU", res["ok"] and tc.probe(os.path.join(TMP, "hw.mp4"))["height"] == 1080)
else:
    print("skip hardware encode (no usable VAAPI device here)")

res = tc.transcode(src, os.path.join(TMP, "h264.mp4"), quality=26, hw_mode="off", codec="h264", preset="veryfast")
o264 = tc.probe(os.path.join(TMP, "h264.mp4"))
check("H.264 on the CPU (veryfast): 1080p h264, audio kept", res["ok"] and res["codec"] == "h264" and o264["vcodec"] == "h264" and o264["height"] == 1080)
cmd = tc.build_command("a.mp4", "b.mp4", {**info, "hdr": False}, 24, False, "h264", "veryfast")
check("H.264 command: libx264, CRF = quality - 4, chosen preset, no hvc1 tag", "libx264" in cmd and cmd[cmd.index("-crf") + 1] == "20" and cmd[cmd.index("-preset") + 1] == "veryfast" and "hvc1" not in cmd)
cmd = tc.build_command("a.mp4", "b.mp4", {**info, "hdr": False}, 24, False, "h265", "bogus")
check("H.265 command: libx265 CRF as given, unknown preset -> fast, hvc1 tag", "libx265" in cmd and cmd[cmd.index("-crf") + 1] == "24" and cmd[cmd.index("-preset") + 1] == "fast" and "hvc1" in cmd)
cmd = tc.build_command("a.mp4", "b.mp4", {**info, "hdr": True, "color": {}}, 24, True, "h264", "veryfast")
check("HDR source always stays 10-bit H.265 on the CPU", "libx265" in cmd and "yuv420p10le" in cmd and "-vaapi_device" not in cmd)
cmd = tc.build_command("a.mp4", "b.mp4", {**info, "hdr": False}, 24, True, "h264", "fast")
check("H.264 VAAPI command: h264_vaapi, QP = CRF + 1", "h264_vaapi" in cmd and cmd[cmd.index("-qp") + 1] == "21")

DB = Database(os.path.join(TMP, "t.sqlite3"))
from routers.settings import get_setting_value
def setting(k, v):
    with DB.connect() as c:
        c.execute("INSERT INTO setting (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", (k, v))
old, young = (date.today() - timedelta(days=12)).isoformat(), (date.today() - timedelta(days=5)).isoformat()
check("default 10 days: a 12-day-old scene may take 2160p and SD", ix.fallback_resolutions(DB, old) == [2160, 480])
check("... a 5-day-old one may not", ix.fallback_resolutions(DB, young) == [])
setting("fallback_accept_sd", "false")
check("SD can be switched off", ix.fallback_resolutions(DB, old) == [2160])
setting("fallback_resolution_days", "0")
check("0 days = off", ix.fallback_resolutions(DB, old) == [])
setting("fallback_resolution_days", "10"); setting("fallback_accept_sd", "true")

with DB.connect() as c:
    pid = quality_profile_repo.create(DB, "Std", [1080, 720], None)
setting("quality_profile_id", str(pid))
profile = quality_profile_repo.get_one(DB, pid)
def make_scene(sid, d):
    with DB.connect() as c:
        release_repo.insert_release(DB, c, {"id": sid, "title": "Sunset Fun", "created": d + "T10:00:00Z", "date": d, "studio": {"id": "st", "name": "Some Studio"}}, "stashdb", "studio", "st1")
        c.execute("UPDATE release SET status='sent_to_whisparr' WHERE scene_stashdb_id = ?", (sid,))
make_scene("old1", old); make_scene("young1", young)
UHD = "SomeStudio.%s.Sunset.Fun.XXX.2160p.MP4-GRP"
class Src:
    name, available = "fake", True
    def __init__(self, d): self.title = UHD % d[2:].replace("-", ".")
    def search(self, q):
        return [{"title": self.title, "downloadUrl": "u", "size": 3e9, "age": 3, "indexer": "i", "indexerId": 1, "protocol": "usenet", "quality": {"quality": {"resolution": 2160}}}]
    def indexer_priorities(self): return {1: 25}
r_old = ix.search_and_grab(DB, None, 1, "stashdb", "old1", "Sunset Fun", old, [], profile, search_source=Src(old), studio_name="Some Studio", dry_run=True)
r_young = ix.search_and_grab(DB, None, 1, "stashdb", "young1", "Sunset Fun", young, [], profile, search_source=Src(young), studio_name="Some Studio", dry_run=True)
check("search: only a 2160p release exists, scene 12 days old -> it is taken", (r_old.get("would_grab") or {}).get("title") == Src(old).title)
check("search: the same for a 5-day-old scene -> not taken (quality not in profile)", not r_young.get("would_grab"))

# the importer's decision + encode on a real file
with DB.connect() as c:
    c.execute("UPDATE release SET grabbed_title = ? WHERE scene_stashdb_id = 'old1'", ("x",))
import threading
done = threading.Event(); fi.on_encode_done = done.set
setting("transcode_hw", "off"); setting("transcode_preset", "veryfast")
first = fi._maybe_reencode(DB, "stashdb", "old1", src)
check("import: a 2160p file is only QUEUED (the import run is not held up)", first == {"status": "waiting_encode"} and fi.encode_active("stashdb", "old1"))
check("... asking again meanwhile does not queue a second job", fi._maybe_reencode(DB, "stashdb", "old1", src) == {"status": "waiting_encode"})
check("import_one's result carries the waiting status", fi._maybe_reencode(DB, "stashdb", "old1", src).get("status") == "waiting_encode")
check("the worker reports back when it is done", done.wait(120) and not fi.encode_active("stashdb", "old1"))
res = fi._maybe_reencode(DB, "stashdb", "old1", src)
check("import: the finished encode is a 1080p copy in DATA_DIR/transcode", res and res["file"].startswith(os.path.join(TMP, "transcode")) and tc.probe(res["file"])["height"] == 1080 and "Re-encoded" in res["detail"])
other = os.path.join(TMP, "other.mp4"); import shutil; shutil.copy(src, other); open(other, "ab").write(b"x")
check("a finished encode is not reused for a different download of the scene", fi._finished_encode("old1", other) is None and fi._finished_encode("old1", src) is None)
check("import: a young scene's 2160p file is left alone (the quality check decides)", fi._maybe_reencode(DB, "stashdb", "young1", src) is None)
with DB.connect() as c:
    c.execute("UPDATE release SET is_vr = 1 WHERE scene_stashdb_id = 'old1'")
check("import: VR is never touched", fi._maybe_reencode(DB, "stashdb", "old1", src) is None)

# a re-encoded scene is finalized without fingerprints
with DB.connect() as c:
    c.execute("UPDATE release SET is_vr = 0, import_error_code = 'reencoded', pending_import_path = '/media/x.mp4' WHERE scene_stashdb_id = 'old1'")
class Stash:
    def __init__(self, scene): self.scene = scene
    def find_scene_by_path(self, p): return self.scene
fi._tag_imported_scene = lambda *a, **k: 77
fi._trigger_generate = lambda *a, **k: None
fi._after_import = lambda *a, **k: None
fi._provider_endpoint = lambda *a, **k: "https://stashdb.org/graphql"
check("finalize: Stash has not scanned it yet -> still pending", fi._finalize_verification(DB, Stash(None), object(), "stashdb", "old1", "/media/x.mp4") is None)
import verification
check("phash distance: equal = 0, 3 bits apart = 3, no phash = 99",
      verification.phash_distance({("phash", "ff00")}, {("phash", "ff00")}) == 0 and verification.phash_distance({("phash", "ff00")}, {("phash", "ff07")}) == 3
      and verification.phash_distance({("oshash", "ab")}, {("phash", "ff00")}) == 99)

class Provider:
    def __init__(self, hashes): self.hashes = hashes
    def get_scene_fingerprints(self, sid): return [{"algorithm": "PHASH", "hash": h} for h in self.hashes]
def scene_with(*fps):
    return {"id": 1, "title": "T", "files": [{"path": "/media/x.mp4", "fingerprints": [{"type": t, "value": v} for t, v in fps]}]}
def reset():
    with DB.connect() as c:
        c.execute("UPDATE release SET status = 'sent_to_whisparr', import_error_code = 'reencoded', pending_import_path = '/media/x.mp4' WHERE scene_stashdb_id = 'old1'")
def final(stash_scene, remote):
    reset()
    out = fi._finalize_verification(DB, Stash(stash_scene), Provider(remote), "stashdb", "old1", "/media/x.mp4")
    return out, release_repo.list_rows_for_scene(DB, "stashdb", "old1")[0]

out, row = final(scene_with(("oshash", "aa"), ("phash", "9af5b3107864325f")), ["9af5b31078223a5f"])
check("finalize: the encode's phash is 4 bits from the provider's -> verified, imported, note kept",
      out and out["status"] == "imported" and row["status"] == "imported" and row["pending_import_path"] is None and row["import_error_code"] == "reencoded")
out, row = final(scene_with(("oshash", "aa"), ("phash", "0000000000000000")), ["ffffffffffffffff"])
check("finalize: a phash far away -> import_failed (fingerprint_mismatch), as for any wrong download",
      out and out["status"] == "import_failed" and row["status"] == "import_failed" and row["import_error_code"] == "fingerprint_mismatch")
out, row = final(scene_with(("oshash", "aa")), ["9af5b31078223a5f"])
check("finalize: no phash computed yet -> still pending", out is None and row["status"] == "sent_to_whisparr")
out, row = final(scene_with(("oshash", "aa"), ("phash", "9af5b3107864325f")), [])
check("finalize: the provider has no fingerprints -> imported without the comparison, note kept",
      out and out["status"] == "imported" and row["import_error_code"] == "reencoded")

print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
