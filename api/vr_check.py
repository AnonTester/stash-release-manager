"""Maintenance check "VR scene, but the file is 2D" (2026-09-26).

Some scenes that should be VR turn out to be a plain flat (mono) video
-- no side-by-side left/right eye, no over/under. This check looks at the
actual picture, because the aspect ratio alone is NOT enough: "half SBS"
stereo files are 16:9 too (e.g. VirtualRealPorn ..._3dh.mp4 at
3840x2160), and an upscaled flat video can be 2:1.

Method (validated live 2026-09-26 on known files): grab one small
grayscale frame (128x64) at 25/50/75 % of the runtime with ffmpeg and
correlate the left half with the right half (side-by-side) and the top
half with the bottom half (over/under). Both eyes of a stereo frame are
nearly the same picture: real SBS files scored 0.65-0.93, flat 16:9
files -0.34..0.21. Verdict on the median of the three frames:
  >= STEREO_MIN  -> 'stereo'   (fine, no finding)
  <  MONO_MAX    -> 'mono'     (finding)
  in between     -> 'unclear'  (finding, "please look at it")
  not stereo, but 2:1/1:1 + a mono/360/180 name marker -> 'vr_mono'
                    (monoscopic 360/180 VR: OK, upgrade candidate)
For 'mono'/'unclear' the local file's phash is compared with the
fingerprints the provider (StashDB/ThePornDB) has registered for that
scene. Found live on the first run: most flat files DO match -- the
providers also list the studios' official 2D versions -- so a match
means "known 2D version of this scene", no match "unknown encode"; it's
shown as information, not used to decide the verdict.

Which scenes: every local Stash scene tagged "Virtual Reality" (incl.
child tags). 'quick' mode only looks at files whose aspect ratio is
neither ~2:1 nor ~1:1 (the suspicious ones, a few minutes); 'full' looks
at all of them (~1.7 s per file over the NAS). Results are cached per
file (path + size + mod time) in `vr_file_check`, so later runs only
analyse new or changed files, and a dismissed finding stays dismissed.
Also run automatically for a single scene after the sidecar imports a
VR release (finding only, per the user's decision).
"""
import history
import json
import logging
import re
import statistics
import subprocess
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

VR_TAG_NAME = "Virtual Reality"
# Calibrated 2026-09-26 on user-confirmed files: "CryptoCUMency Deal"
# scored 0.301 and is 2D, "Black Widow 2020 (A XXX Parody)" scored 0.482
# and is real VR (first guess was 0.5 / 0.3). Also confirmed: in the
# "unclear" band both occur ("Virtual Reality Blow Job Queen" 0.408 = VR,
# "X-Rated After Work Oral" 0.447 = 2D) -- no single cut separates them,
# which is exactly what the band is for. Changing these re-judges the
# stored scores via reapply_thresholds() -- no re-analysis needed.
STEREO_MIN = 0.47
MONO_MAX = 0.35
_FRAME_W, _FRAME_H = 128, 64

_SCENES_QUERY = """
query($t: [ID!]) {
  findScenes(scene_filter: {tags: {value: $t, modifier: INCLUDES, depth: -1}}, filter: {per_page: -1}) {
    scenes {
      id title
      files { path width height size mod_time fingerprints { type value } }
      stash_ids { endpoint stash_id }
    }
  }
}
"""


# Untagged scenes of mostly-VR studios (see VrCheckJob._vr_scenes()).
_STUDIO_UNTAGGED_QUERY = """
query($s: [ID!], $t: [ID!]) {
  findScenes(scene_filter: {studios: {value: $s, modifier: INCLUDES},
                            tags: {value: $t, modifier: EXCLUDES, depth: -1}}, filter: {per_page: -1}) {
    scenes {
      id title
      files { path width height size mod_time fingerprints { type value } }
      stash_ids { endpoint stash_id }
    }
  }
}
"""


# --- frame analysis -------------------------------------------------------

def _duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
                         capture_output=True, text=True, timeout=120)
    return float(json.loads(out.stdout or "{}").get("format", {}).get("duration") or 0)


def _frame(path, t):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
         "-vf", f"scale={_FRAME_W}:{_FRAME_H}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, timeout=180).stdout
    return raw if len(raw) >= _FRAME_W * _FRAME_H else None


def _corr(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((y - mb) ** 2 for y in b) ** 0.5
    return num / (da * db) if da and db else 0.0


def _half_scores(raw):
    w, h = _FRAME_W, _FRAME_H
    left = [raw[y * w + x] for y in range(h) for x in range(w // 2)]
    right = [raw[y * w + x + w // 2] for y in range(h) for x in range(w // 2)]
    top = [raw[y * w + x] for y in range(h // 2) for x in range(w)]
    bottom = [raw[(y + h // 2) * w + x] for y in range(h // 2) for x in range(w)]
    return _corr(left, right), _corr(top, bottom)


def analyse_file(local_path):
    """{'lr', 'tb', 'verdict'} for one local file, or {'verdict': 'error',
    'error': ...}. Medians over three frames."""
    try:
        duration = _duration(local_path)
        if duration <= 0:
            return {"verdict": "error", "error": "no duration (not a readable video?)"}
        lrs, tbs = [], []
        for frac in (0.25, 0.5, 0.75):
            raw = _frame(local_path, duration * frac)
            if raw is None:
                continue
            lr, tb = _half_scores(raw)
            lrs.append(lr)
            tbs.append(tb)
        if not lrs:
            return {"verdict": "error", "error": "no frame could be decoded"}
        lr, tb = statistics.median(lrs), statistics.median(tbs)
        return {"lr": round(lr, 3), "tb": round(tb, 3)}
    except Exception as e:
        return {"verdict": "error", "error": f"{type(e).__name__}: {e}"}


# Monoscopic 360/180 VR (2026-09-26, user decision): an equirectangular
# single-image VR file (2:1 for 360, ~1:1 for 180) is real VR -- OK to
# keep, but an UPGRADE candidate (replace with a stereoscopic version,
# see the planned cutoff/upgrade search). Recognised by aspect ratio PLUS
# a name marker, so a flat video that merely has a 2:1 frame isn't
# waved through as VR.
_MONO_VR_MARKER = re.compile(r"(?i)(?:\bmono\b|_mono_|\b360\b|_360|360[_ ]?vr|\b180\b|_180)")


def verdict_for(lr, tb, width=None, height=None, name=""):
    best = max(lr, tb)
    if best >= STEREO_MIN:
        return "stereo"
    ratio = (width / height) if width and height else 0
    equirect = 1.8 <= ratio <= 2.2 or 0.9 <= ratio <= 1.1
    if equirect and _MONO_VR_MARKER.search(name or ""):
        return "vr_mono"
    return "mono" if best < MONO_MAX else "unclear"


def reapply_thresholds(db):
    """Re-judges every stored score with the current thresholds (run at
    startup, cheap). A finding the user dismissed stays dismissed; one
    that is no longer mono/unclear disappears, a new one shows up as
    pending. Returns how many rows changed verdict."""
    changed = 0
    with db.connect() as conn:
        rows = conn.execute("""SELECT rowid, lr_score, tb_score, verdict, finding_status, width, height,
                                      file_path, scene_title FROM vr_file_check
                               WHERE lr_score IS NOT NULL AND tb_score IS NOT NULL AND verdict_manual = 0""").fetchall()
        for r in rows:
            verdict = verdict_for(r["lr_score"], r["tb_score"], r["width"], r["height"],
                                  f"{r['file_path']} {r['scene_title']}")
            if verdict == r["verdict"]:
                continue
            finding = verdict in FINDING_VERDICTS
            status = r["finding_status"]
            if status not in ("dismissed", "fixed"):
                status = "pending" if finding else "none"
            conn.execute("UPDATE vr_file_check SET verdict = ?, finding_status = ? WHERE rowid = ?",
                         (verdict, status, r["rowid"]))
            changed += 1
    if changed:
        logger.info("VR check: %d stored verdict(s) re-judged with thresholds stereo>=%s / 2D<%s",
                    changed, STEREO_MIN, MONO_MAX)
    return changed


def frame_jpeg(local_path, width=640):
    """One JPEG frame from the middle of the file, for the Maintenance
    page's "Frame from file" button -- Stash's own screenshot is often
    the scene's cover image, not a frame of this file."""
    t = _duration(local_path) * 0.5
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", local_path, "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "4", "-f", "image2", "-c:v", "mjpeg", "-"],
        capture_output=True, timeout=180).stdout
    if not out:
        raise RuntimeError("no frame could be decoded")
    return out


def is_suspicious_ratio(width, height):
    """Neither ~2:1 (SBS) nor ~1:1 / ~1:2 (over/under, fisheye)."""
    if not width or not height:
        return True
    r = width / height
    return not (1.8 <= r <= 2.2 or 0.9 <= r <= 1.1 or 0.45 <= r <= 0.55)


# --- persistence ------------------------------------------------------------

def _get_row(db, scene_id, path):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM vr_file_check WHERE scene_id = ? AND file_path = ?",
                           (str(scene_id), path)).fetchone()
    return dict(row) if row else None


# Verdicts shown in the findings list ('vr_mono' as an upgrade candidate).
FINDING_VERDICTS = ("mono", "unclear", "vr_mono")


def _save(db, scene, file, result, phash_match, provider, provider_scene_id):
    with db.connect() as conn:
        prev = conn.execute("SELECT finding_status, verdict, verdict_manual, file_size, file_mod_time FROM vr_file_check "
                            "WHERE scene_id = ? AND file_path = ?", (str(scene["id"]), file["path"])).fetchone()
        # A verdict the user set by hand stays while the file is the same (size + mod time); a changed file is judged afresh.
        manual = int(bool(prev and prev["verdict_manual"] and prev["file_size"] == file.get("size")
                          and prev["file_mod_time"] == file.get("mod_time")))
        if manual:
            result = {**result, "verdict": prev["verdict"]}
        finding = result["verdict"] in FINDING_VERDICTS
        # A dismissal survives re-analysis of the same file; a changed
        # file (new size/mtime) is judged afresh.
        status = "pending" if finding else "none"
        if prev and prev["finding_status"] == "dismissed" and finding:
            status = "dismissed"
        conn.execute(
            """INSERT INTO vr_file_check (scene_id, file_path, scene_title, file_size, file_mod_time, width, height,
                                          lr_score, tb_score, verdict, error, phash_match, provider,
                                          provider_scene_id, finding_status, verdict_manual, checked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT (scene_id, file_path) DO UPDATE SET
                 scene_title = excluded.scene_title, file_size = excluded.file_size,
                 file_mod_time = excluded.file_mod_time, width = excluded.width, height = excluded.height,
                 lr_score = excluded.lr_score, tb_score = excluded.tb_score, verdict = excluded.verdict,
                 error = excluded.error, phash_match = excluded.phash_match, provider = excluded.provider,
                 provider_scene_id = excluded.provider_scene_id, finding_status = excluded.finding_status,
                 verdict_manual = excluded.verdict_manual, checked_at = excluded.checked_at""",
            (str(scene["id"]), file["path"], scene.get("title") or "", file.get("size"), file.get("mod_time"),
             file.get("width"), file.get("height"), result.get("lr"), result.get("tb"), result["verdict"],
             result.get("error"), None if phash_match is None else int(phash_match), provider, provider_scene_id,
             status, manual),
        )


def list_findings(db, status="pending"):
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT rowid AS id, * FROM vr_file_check WHERE finding_status = ? ORDER BY verdict DESC, scene_title""",
            (status,)).fetchall()
    return [dict(r) for r in rows]


def set_finding_status(db, finding_id, status):
    with db.connect() as conn:
        conn.execute("UPDATE vr_file_check SET finding_status = ? WHERE rowid = ?", (status, int(finding_id)))


MANUAL_VERDICTS = ("mono", "vr_mono", "unclear")  # what the user can choose: 2D / VR mono / unclear


def set_verdict(db, finding_id, verdict, manual=True):
    """The user's own verdict for a file (v0.105.0). It is a finding again (pending) -- a dismissal was a different decision.
    Returns the state before, for the Undo."""
    with db.connect() as conn:
        row = conn.execute("SELECT verdict, verdict_manual, finding_status FROM vr_file_check WHERE rowid = ?",
                           (int(finding_id),)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE vr_file_check SET verdict = ?, verdict_manual = ?, finding_status = 'pending' WHERE rowid = ?",
                     (verdict, int(bool(manual)), int(finding_id)))
    return dict(row)


def restore_verdict(db, finding_id, verdict, manual, finding_status):
    with db.connect() as conn:
        conn.execute("UPDATE vr_file_check SET verdict = ?, verdict_manual = ?, finding_status = ? WHERE rowid = ?",
                     (verdict, int(bool(manual)), finding_status, int(finding_id)))


def prune_stale(db, stash_client):
    """Drops pending/dismissed findings whose scene is gone from Stash or no longer has that file (e.g. after an upgrade merged the old
    scene into the new one). Derived data -- a later check recreates whatever still exists. One Stash request. Returns the removed
    rows (title, path) for the log."""
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT rowid AS id, scene_id, scene_title, file_path FROM vr_file_check WHERE finding_status IN ('pending', 'dismissed')")]
    if not rows:
        return []
    ids = sorted({r["scene_id"] for r in rows}, key=int)
    # findScene answers null for a missing id (findScenes(scene_ids) would raise), all ids as aliases in ONE request.
    query = "query { " + " ".join(f's{i}: findScene(id: "{i}") {{ id files {{ path }} }}' for i in ids) + " }"
    data = stash_client._graphql(query, {})
    files = {i: {f["path"] for f in ((data.get(f"s{i}") or {}).get("files") or [])} for i in ids}
    stale = [r for r in rows if r["file_path"] not in files.get(r["scene_id"], set())]
    if stale:
        with db.connect() as conn:
            conn.executemany("DELETE FROM vr_file_check WHERE rowid = ?", [(r["id"],) for r in stale])
        for r in stale:
            history.event("info", "vr", f"VR finding removed: scene or file no longer exists ({r['scene_title']})")
    return stale


def get_finding(db, finding_id):
    with db.connect() as conn:
        row = conn.execute("SELECT rowid AS id, * FROM vr_file_check WHERE rowid = ?", (int(finding_id),)).fetchone()
    return dict(row) if row else None


# --- checking ---------------------------------------------------------------

def _provider_ref(scene, endpoints):
    """(provider, provider scene id) of the scene's first linked provider,
    StashDB preferred."""
    ids = {s["endpoint"]: s["stash_id"] for s in scene.get("stash_ids") or []}
    for provider, endpoint in endpoints:
        if endpoint in ids:
            return provider, ids[endpoint]
    return None, None


def _phash_match(file, provider_client, provider_scene_id):
    """True/False when both sides have phashes, None when unknown."""
    local = {fp["value"] for fp in file.get("fingerprints") or [] if fp["type"].lower() == "phash" and fp.get("value")}
    if not local or provider_client is None or not provider_scene_id:
        return None
    try:
        remote = {fp["hash"] for fp in provider_client.get_scene_fingerprints(provider_scene_id)
                  if fp.get("algorithm", "").lower() == "phash" and fp.get("hash")}
    except Exception:
        logger.warning("vr check: fingerprint lookup failed for %s", provider_scene_id, exc_info=True)
        return None
    if not remote:
        return None
    return bool(local & remote)


class VrCheckJob:
    """One run at a time; start() for the Maintenance page (background),
    check_scene() for the post-import hook."""

    def __init__(self, db, get_stash_client, get_provider_client, to_local_path):
        self.db = db
        self.get_stash_client = get_stash_client
        self.get_provider_client = get_provider_client  # provider key -> client or None
        self.to_local_path = to_local_path              # Stash path -> sidecar path
        self._lock = threading.Lock()
        self.status = {"running": False}

    def _vr_scenes(self, stash_client):
        """VR-tagged scenes plus (2026-09-26, user: "2D scenes of a VR studio
        must be upgraded to real VR too") the UNtagged scenes of studios
        that are mostly VR -- at least half of their local scenes carry the
        VR tag."""
        tag_id = stash_client.find_tag_id(VR_TAG_NAME)
        if not tag_id:
            return []
        scenes = stash_client._graphql(_SCENES_QUERY, {"t": [str(tag_id)]})["findScenes"]["scenes"]
        vr_studios = self._vr_dominant_studios(stash_client, tag_id)
        if vr_studios:
            extra = stash_client._graphql(_STUDIO_UNTAGGED_QUERY, {"s": sorted(vr_studios), "t": [str(tag_id)]})
            scenes += extra["findScenes"]["scenes"]
        return scenes

    def _vr_dominant_studios(self, stash_client, tag_id):
        data = stash_client._graphql(
            "query($t: [ID!]) { findScenes(scene_filter: {tags: {value: $t, modifier: INCLUDES, depth: -1}}, "
            "filter: {per_page: -1}) { scenes { studio { id scene_count } } } }", {"t": [str(tag_id)]})
        vr_counts, totals = {}, {}
        for s in data["findScenes"]["scenes"]:
            st = s.get("studio")
            if st:
                vr_counts[st["id"]] = vr_counts.get(st["id"], 0) + 1
                totals[st["id"]] = st["scene_count"]
        return {sid for sid, n in vr_counts.items() if totals.get(sid) and n / totals[sid] >= 0.5 and n < totals[sid]}

    def _endpoints(self, stash_client):
        return [("stashdb", stash_client.stashdb_endpoint), ("theporndb", stash_client.theporndb_endpoint)]

    def _check_file(self, stash_client, scene, file, force=False):
        cached = _get_row(self.db, scene["id"], file["path"])
        if (not force and cached and cached["file_size"] == file.get("size")
                and cached["file_mod_time"] == file.get("mod_time") and cached["verdict"] != "error"):
            return cached["verdict"], False
        result = analyse_file(self.to_local_path(file["path"]))
        if "lr" in result:
            result["verdict"] = verdict_for(result["lr"], result["tb"], file.get("width"), file.get("height"),
                                            f"{file['path']} {scene.get('title') or ''}")
        provider, provider_scene_id = _provider_ref(scene, self._endpoints(stash_client))
        phash = None
        if result["verdict"] in ("mono", "unclear"):
            phash = _phash_match(file, self.get_provider_client(provider) if provider else None, provider_scene_id)
        _save(self.db, scene, file, result, phash, provider, provider_scene_id)
        return result["verdict"], True

    def run(self, mode="quick", force=False):
        if not self._lock.acquire(blocking=False):
            return {"skipped": True, "reason": "a VR check is already running"}
        counts = {"stereo": 0, "mono": 0, "unclear": 0, "vr_mono": 0, "error": 0}
        analysed = 0
        try:
            self.status = {"running": True, "mode": mode, "phase": "listing", "current": 0, "total": 0,
                           "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            stash_client = self.get_stash_client()
            work = []
            for scene in self._vr_scenes(stash_client):
                for file in scene.get("files") or []:
                    if mode == "full" or is_suspicious_ratio(file.get("width"), file.get("height")):
                        work.append((scene, file))
            self.status.update(phase="checking", total=len(work))
            for i, (scene, file) in enumerate(work):
                self.status.update(current=i + 1, title=scene.get("title"))
                verdict, fresh = self._check_file(stash_client, scene, file, force)
                counts[verdict] = counts.get(verdict, 0) + 1
                analysed += int(fresh)
            summary = {"mode": mode, "files": len(work), "analysed": analysed, **counts,
                       "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            with self.db.connect() as conn:
                conn.execute("""INSERT INTO setting (key, value) VALUES ('vr_check_last_run', ?)
                                ON CONFLICT (key) DO UPDATE SET value = excluded.value""", (json.dumps(summary),))
            logger.info("VR check: %s", summary)
            return summary
        except Exception as e:
            logger.exception("VR check failed")
            return {"error": f"{type(e).__name__}: {e}"}
        finally:
            self.status = {"running": False}
            self._lock.release()

    def start(self, mode="quick", force=False):
        if self.status.get("running"):
            return {"started": False, "reason": "already running", **self.status}
        threading.Thread(target=self.run, args=(mode, force), daemon=True).start()
        return {"started": True}

    def check_scene(self, scene_id, only_path=None):
        """Post-import: analyse one freshly imported scene's files (if it
        is VR-tagged) -- with `only_path`, just that file (2026-09-26: after
        an upgrade merge the scene still lists the replaced file until
        Stash's Clean ran; judging that one wrongly blocklisted the new,
        good release). Best-effort, never raises."""
        try:
            stash_client = self.get_stash_client()
            tag_id = stash_client.find_tag_id(VR_TAG_NAME)
            scene = stash_client._graphql(
                "query($id: ID!) { findScene(id: $id) { id title tags { id } "
                "files { path width height size mod_time fingerprints { type value } } "
                "stash_ids { endpoint stash_id } } }", {"id": str(scene_id)})["findScene"]
            if not scene or not tag_id:
                return None
            if str(tag_id) not in {t["id"] for t in scene.get("tags") or []}:
                return None
            files = [f for f in scene.get("files") or [] if only_path is None or f["path"] == only_path]
            verdicts = [self._check_file(stash_client, scene, f)[0] for f in files]
            if any(v in ("mono", "unclear") for v in verdicts):
                logger.warning("VR check: freshly imported scene %s (%s) looks 2D: %s",
                               scene_id, scene.get("title"), verdicts)
                history.add("warning", "history", "vr_check", "Freshly imported VR scene looks 2D/mono",
                            scene_title=scene.get("title"), details={"stash_scene": scene_id, "verdicts": verdicts})
            return verdicts
        except Exception:
            logger.exception("VR check for imported scene %s failed", scene_id)
            return None


def last_run(db):
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM setting WHERE key = 'vr_check_last_run'").fetchone()
    try:
        return json.loads(row["value"]) if row else None
    except ValueError:
        return None
