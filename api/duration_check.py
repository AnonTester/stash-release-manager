"""Maintenance check "Duration mismatch" (v0.209.0): library scenes whose file is clearly shorter or longer than the provider says.

Why: a sample or a cut download is identified by fingerprint like the full scene, and ThePornDB's fingerprints are not reliable enough to
tell them apart (tester report). The one hard fact that still separates them is the length. Providers maintain it for most scenes, not
all ("as far as maintained"): a scene without a provider length is simply not checked.

One row per local scene in `duration_check`: the primary file's length against the provider's length. The deviation threshold (default
5 %) is applied when the list is read, so changing it needs no new run. The run walks the library once (Stash pages), asks the provider
only for scenes it has no recent answer for, and stores every answer -- also "no length" -- so the next run is cheap.
"""
import logging
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

PAGE = 500                 # Stash scenes per request
STASHDB_BATCH = 10         # scenes per StashDB request (aliased findScene; 25 ran into the 30 s read timeout once)
RECHECK_AFTER_DAYS = 30    # a stored provider answer (also "no length") is trusted this long
SCENES_QUERY = """
query($f: FindFilterType) {
  findScenes(filter: $f) {
    count
    scenes { id title files { path size duration } stash_ids { endpoint stash_id } }
  }
}
"""


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def provider_ref(scene, endpoints):
    """(provider, provider scene id) of the first known provider id of a Stash scene, else (None, None)."""
    ids = {s["endpoint"]: s["stash_id"] for s in scene.get("stash_ids") or []}
    for provider, endpoint in endpoints:
        if endpoint and endpoint in ids:
            return provider, ids[endpoint]
    return None, None


def deviation(file_sec, provider_sec):
    """(file - provider) / provider: -0.4 = the file is 40 % shorter than the provider's length. None when either is missing."""
    if not file_sec or not provider_sec:
        return None
    return (float(file_sec) - float(provider_sec)) / float(provider_sec)


def list_findings(db, threshold_pct=5.0, direction="", include_ignored=False):
    """Rows whose deviation is above the threshold, the biggest first. direction: '' | 'shorter' | 'longer'."""
    t = abs(float(threshold_pct)) / 100.0
    where = ["file_sec > 0", "provider_sec > 0", "ABS(file_sec - provider_sec) / provider_sec > ?"]
    params = [t]
    if direction == "shorter":
        where.append("file_sec < provider_sec")
    elif direction == "longer":
        where.append("file_sec > provider_sec")
    if not include_ignored:
        where.append("status != 'ignored'")
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM duration_check WHERE {' AND '.join(where)} "
            "ORDER BY ABS(file_sec - provider_sec) / provider_sec DESC", params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["deviation"] = deviation(d["file_sec"], d["provider_sec"])
        out.append(d)
    return out


def summary(db):
    with db.connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM duration_check").fetchone()[0]
        with_len = conn.execute("SELECT COUNT(*) FROM duration_check WHERE provider_sec > 0 AND file_sec > 0").fetchone()[0]
        last = conn.execute("SELECT MAX(checked_at) FROM duration_check").fetchone()[0]
    return {"checked": total, "with_provider_length": with_len, "last_checked": last}


def get_row(db, scene_id):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM duration_check WHERE scene_id = ?", (str(scene_id),)).fetchone()
    return dict(row) if row else None


def set_status(db, scene_id, status):
    with db.connect() as conn:
        conn.execute("UPDATE duration_check SET status = ? WHERE scene_id = ?", (status, str(scene_id)))


def _save(db, scene, file, provider, pid, provider_sec):
    with db.connect() as conn:
        old = conn.execute("SELECT status FROM duration_check WHERE scene_id = ?", (str(scene["id"]),)).fetchone()
        conn.execute(
            """INSERT INTO duration_check (scene_id, file_path, file_size, scene_title, provider, provider_scene_id, file_sec, provider_sec,
                                           status, checked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(scene_id) DO UPDATE SET file_path = excluded.file_path, file_size = excluded.file_size,
                 scene_title = excluded.scene_title, provider = excluded.provider, provider_scene_id = excluded.provider_scene_id,
                 file_sec = excluded.file_sec, provider_sec = excluded.provider_sec, checked_at = excluded.checked_at""",
            (str(scene["id"]), file["path"], file.get("size"), scene.get("title") or "", provider, pid,
             file.get("duration"), provider_sec, (old["status"] if old else "checked"), _now()))


class DurationCheckJob:
    """One run at a time, in the background (Maintenance page "Run check"). `status` is polled by the page."""

    def __init__(self, db, get_stash_client, get_provider_client):
        self.db = db
        self.get_stash_client = get_stash_client
        self.get_provider_client = get_provider_client
        self._lock = threading.Lock()
        self.status = {"running": False}

    def start(self, force=False):
        if self.status.get("running"):
            return {"skipped": True, "reason": "a duration check is already running"}
        threading.Thread(target=self.run, args=(force,), daemon=True, name="duration-check").start()
        return {"started": True}

    def _library(self, stash_client, endpoints):
        """Every local scene that has a provider id and a file: [(scene, file, provider, pid)]."""
        out, page = [], 1
        while True:
            data = stash_client._graphql(SCENES_QUERY, {"f": {"page": page, "per_page": PAGE, "sort": "id", "direction": "ASC"}})["findScenes"]
            scenes = data["scenes"]
            for sc in scenes:
                provider, pid = provider_ref(sc, endpoints)
                files = sc.get("files") or []
                if provider and files:
                    out.append((sc, files[0], provider, pid))
            self.status.update(phase="reading the library", done=min(page * PAGE, data["count"]), total=data["count"])
            if len(scenes) < PAGE:
                return out
            page += 1

    def run(self, force=False):
        if not self._lock.acquire(blocking=False):
            return {"skipped": True, "reason": "a duration check is already running"}
        self.status = {"running": True, "phase": "reading the library", "done": 0, "total": 0, "asked": 0, "errors": 0,
                       "started_at": _now()}
        try:
            stash_client = self.get_stash_client()
            endpoints = [("stashdb", stash_client.stashdb_endpoint), ("theporndb", stash_client.theporndb_endpoint)]
            library = self._library(stash_client, endpoints)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=RECHECK_AFTER_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
            with self.db.connect() as conn:
                stored = {r["scene_id"]: dict(r) for r in conn.execute("SELECT * FROM duration_check")}
            todo = {"stashdb": [], "theporndb": []}
            for sc, f, provider, pid in library:
                old = stored.get(str(sc["id"]))
                fresh = (old and not force and old["provider"] == provider and old["provider_scene_id"] == pid
                         and old["checked_at"] >= cutoff)
                if fresh:
                    # The provider's answer is still good; the FILE may have changed (replaced), which is cheap to keep current.
                    if old["file_path"] != f["path"] or old["file_size"] != f.get("size") or old["file_sec"] != f.get("duration"):
                        _save(self.db, sc, f, provider, pid, old["provider_sec"])
                    continue
                todo[provider].append((sc, f, pid))
            self.status.update(phase="asking the providers", done=0, total=sum(len(v) for v in todo.values()))
            for provider, items in todo.items():
                client = self.get_provider_client(provider) if items else None
                if not items:
                    continue
                if client is None:
                    logger.warning("duration check: %s is not configured, %d scene(s) skipped", provider, len(items))
                    continue
                step = STASHDB_BATCH if provider == "stashdb" else 1
                for i in range(0, len(items), step):
                    chunk = items[i:i + step]
                    try:
                        answers = client.scene_durations([pid for _, _, pid in chunk])
                    except Exception:
                        logger.exception("duration check: %s lookup failed (those scenes are tried again next run)", provider)
                        self.status["errors"] += 1
                        self.status["done"] += len(chunk)
                        continue
                    for sc, f, pid in chunk:
                        _save(self.db, sc, f, provider, pid, answers.get(str(pid)))
                    self.status["asked"] += len(chunk)
                    self.status["done"] += len(chunk)
            # Scenes that left the library (or lost their provider id) no longer belong in the list.
            keep = {str(sc["id"]) for sc, _, _, _ in library}
            with self.db.connect() as conn:
                for sid in set(stored) - keep:
                    conn.execute("DELETE FROM duration_check WHERE scene_id = ?", (sid,))
            self.status.update(phase="done", finished_at=_now())
            logger.info("duration check done: %s", {k: v for k, v in self.status.items() if k in ("asked", "errors", "total")})
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            logger.exception("duration check failed")
            self.status.update(phase="failed", error=f"{type(e).__name__}: {e}")
            return {"error": str(e)}
        finally:
            self.status["running"] = False
            self._lock.release()
