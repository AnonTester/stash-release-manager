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
        stash_client = self.get_stash_client()
        findings = duration_check.list_findings(self.db, threshold_pct=DURATION_THRESHOLD_PCT)  # as the Duration mismatch page's default
        added = skipped = 0
        self.status.update(done=0, total=len(findings))
        for i, row in enumerate(findings):
            self.status["done"] = i
            with self.db.connect() as conn:
                have = conn.execute("SELECT 1 FROM upgrade_candidate WHERE scene_id = ? AND file_path = ?",
                                    (str(row["scene_id"]), row.get("file_path"))).fetchone()
            if have:
                continue
            try:
                out = upgrades.add_manual_candidate(self.db, stash_client, row["scene_id"], "duration", row.get("provider_sec"))
            except Exception as e:  # noqa: BLE001 -- e.g. the scene was deleted in Stash since the duration check read it (v0.237.6)
                logger.warning("library check: scene %s skipped (%s)", row["scene_id"], e)
                skipped += 1
                continue
            if out.get("id"):
                # Not searched automatically: a length that disagrees with the provider's is as often the provider's mistake as the file's, so
                # these wait for a look ('check', v0.237.8) -- "Queue for search" releases them, "To wishlist" / Dismiss decide at once.
                upgrades.set_candidate(self.db, out["id"], status="check")
                added += 1
        self.status.update(skipped_duration=skipped)
        return added
