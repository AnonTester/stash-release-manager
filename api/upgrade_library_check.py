"""One-time check of the WHOLE library for upgrade candidates (v0.237.2, Maintenance > Upgrade wanted > "Check the whole library").

The normal candidate list only knows what the VR check and the cutoff query find. This job, started by the user, looks at every scene Stash has:
  1. the duration check over the whole library (api/duration_check.py: the provider's length against the file's, one answer per scene, cached for
     30 days) -- a file clearly shorter / longer than the provider says becomes a candidate with the reason 'duration' (unless that file already
     is a candidate, or the finding was ignored on the Duration mismatch page);
  2. the candidate refresh (upgrades.refresh_candidates): VR files with a 2D / mono verdict or below `vr_upgrade_min_mbps`, and files below
     the quality profile's cutoff resolution (scenes in Groups -- films -- left out).
It runs in the background; the page polls `status`."""
import json
import logging
import threading
import time
from datetime import datetime, timezone

import duration_check
import upgrades

logger = logging.getLogger(__name__)

DURATION_THRESHOLD_PCT = 5.0


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class UpgradeLibraryCheck:
    def __init__(self, db, get_stash_client, duration_job, get_setting, get_cutoff):
        self.db = db
        self.get_stash_client = get_stash_client
        self.duration_job = duration_job
        self.get_setting = get_setting
        self.get_cutoff = get_cutoff
        self._lock = threading.Lock()
        self.status = {"running": False}

    def start(self, force=False):
        if self.status.get("running"):
            return {"skipped": True, "reason": "the library check is already running"}
        threading.Thread(target=self.run, args=(force,), daemon=True, name="upgrade-library-check").start()
        return {"started": True}

    def _wait_for_duration_job(self):
        """Mirrors the duration job's progress into this job's status until it is done."""
        while True:
            d = dict(self.duration_job.status)
            self.status.update(phase=f"duration check: {d.get('phase', '')}", done=d.get("done", 0), total=d.get("total", 0))
            if not d.get("running"):
                return d
            time.sleep(2)

    def run(self, force=False):
        if not self._lock.acquire(blocking=False):
            return {"skipped": True, "reason": "the library check is already running"}
        self.status = {"running": True, "phase": "starting", "done": 0, "total": 0, "started_at": _now()}
        try:
            started = self.duration_job.start(force)
            if started.get("skipped"):  # one is running already (started on the Duration mismatch page): wait for it
                logger.info("library check: joining a duration check that is already running")
            d = self._wait_for_duration_job()
            if d.get("phase") == "failed":
                raise RuntimeError(f"the duration check failed: {d.get('error')}")
            self.status.update(phase="adding wrong-length files", done=0, total=0)
            added = self._add_duration_candidates()
            self.status.update(phase="looking for VR and low-quality files")
            stash_client = self.get_stash_client()
            counts = upgrades.refresh_candidates(self.db, stash_client, self.get_setting, self.get_cutoff())
            self.status.update(phase="done", finished_at=_now(), added_duration=added, counts=counts, cutoff=self.get_cutoff())
            self._remember()
            logger.info("upgrade library check done: %d wrong-length candidate(s) added, %s", added, counts)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            logger.exception("upgrade library check failed")
            self.status.update(phase="failed", error=f"{type(e).__name__}: {e}")
            return {"error": str(e)}
        finally:
            self.status["running"] = False
            self._lock.release()

    def _remember(self):
        """The result of the last finished run is kept in the settings table: a new page view (or a restart) shows it, no new check is needed."""
        keep = {k: self.status.get(k) for k in ("finished_at", "added_duration", "skipped_duration", "counts", "cutoff")}
        with self.db.connect() as conn:
            conn.execute("""INSERT INTO setting (key, value) VALUES ('upgrade_library_check_last', ?)
                            ON CONFLICT (key) DO UPDATE SET value = excluded.value""", (json.dumps(keep),))

    def last_run(self):
        """The kept result of the last finished run, or None."""
        with self.db.connect() as conn:
            row = conn.execute("SELECT value FROM setting WHERE key = 'upgrade_library_check_last'").fetchone()
        try:
            return json.loads(row["value"]) if row else None
        except ValueError:
            return None

    def _add_duration_candidates(self):
        result = sync_duration_candidates(self.db, self.get_stash_client(), duration_threshold(self.db), self.status)
        self.status.update(skipped_duration=result["skipped"])
        return result["added"]


def duration_threshold(db):
    """The deviation (percent) from which a file counts as "wrong length": the setting duration_threshold_pct, default 5 (v0.241.0; the Duration
    mismatch page that held it moved into Upgrade wanted)."""
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM setting WHERE key = 'duration_threshold_pct'").fetchone()
    try:
        return max(1.0, min(90.0, float(row["value"]))) if row else DURATION_THRESHOLD_PCT
    except (TypeError, ValueError):
        return DURATION_THRESHOLD_PCT


def set_duration_threshold(db, pct):
    pct = max(1.0, min(90.0, float(pct)))
    with db.connect() as conn:
        conn.execute("""INSERT INTO setting (key, value) VALUES ('duration_threshold_pct', ?)
                        ON CONFLICT (key) DO UPDATE SET value = excluded.value""", (str(pct),))
    return pct


def sync_duration_candidates(db, stash_client, threshold_pct, status=None):
    """Makes the 'wrong length' candidates of Upgrade wanted match the stored length comparison at this threshold (v0.241.0): every scene outside
    the movie groups (those have their own page) that deviates more than the threshold, was not dismissed and is no candidate yet becomes one,
    waiting in the state 'check' (not searched automatically: a length that disagrees with the provider's is as often the provider's mistake as the
    file's -- "Queue for search" releases it, "To wishlist" / Dismiss decide at once). Candidates still in 'check' that no longer qualify (a higher
    threshold, a replaced file) are dropped. Decided candidates are never touched. Returns {added, removed, skipped}."""
    findings = duration_check.list_findings(db, threshold_pct=threshold_pct, groups="exclude")
    # A file parked in _UNMATCHED is not identified yet (it waits in Review: Identify Scene): its length says nothing about an upgrade.
    findings = [f for f in findings if "/_UNMATCHED/" not in (f.get("file_path") or "")]
    added = skipped = 0
    if status is not None:
        status.update(done=0, total=len(findings))
    for i, row in enumerate(findings):
        if status is not None:
            status["done"] = i
        with db.connect() as conn:
            have = conn.execute("SELECT 1 FROM upgrade_candidate WHERE scene_id = ? AND file_path = ?",
                                (str(row["scene_id"]), row.get("file_path"))).fetchone()
        if have:
            continue
        try:
            out = upgrades.add_manual_candidate(db, stash_client, row["scene_id"], "duration", row.get("provider_sec"))
        except Exception as e:  # noqa: BLE001 -- e.g. the scene was deleted in Stash since the duration check read it (v0.237.6)
            logger.warning("duration candidates: scene %s skipped (%s)", row["scene_id"], e)
            skipped += 1
            continue
        if out.get("id"):
            upgrades.set_candidate(db, out["id"], status="check")
            added += 1
    keep = {str(r["scene_id"]) for r in findings}
    removed = 0
    with db.connect() as conn:
        for r in conn.execute("SELECT id, scene_id FROM upgrade_candidate WHERE reason = 'duration' AND status = 'check'").fetchall():
            if str(r["scene_id"]) not in keep:
                conn.execute("DELETE FROM upgrade_candidate WHERE id = ?", (r["id"],))
                removed += 1
    return {"added": added, "removed": removed, "skipped": skipped}
