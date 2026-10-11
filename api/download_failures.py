"""A download SABnzbd reports as 'Failed' (v0.94.2, 2026-10-03).

Until then only 'Completed' jobs were looked at: a failed one (e.g. a par2 repair that could not finish) left its release on
'sent_to_whisparr' with a download job for ever -- Activity showed "Download complete -- waiting for import" and nothing happened
(found with the 2700p upgrade of "A Pussy Work Out", 8.3 GB, failed 2026-10-02). Now:
  * the failed release title goes on the scene's own blocklist (release.review_rejected_titles), so no later search grabs it again;
  * the release loses its download job and stays on the wishlist ('sent_to_whisparr'), the periodic search tries another release;
  * an UPGRADE grab (release.upgrade_of): the upgrade candidate goes back to 'wanted' (title added to its rejected titles), the
    release rows are put back as they were (the pseudo 'upgrade:' row is removed), the next upgrade search tries another release;
  * a History entry says what failed and why.
"""
import json
import logging

import history
import release_repo
import upgrades

logger = logging.getLogger(__name__)


def handle_failed(db, release, job):
    """`release` = the release row that holds the download job, `job` = SABnzbd's history entry (status 'Failed'). Returns a short summary."""
    provider, pid = release["provider"], release["scene_stashdb_id"]
    title = (job or {}).get("name")
    reason = ((job or {}).get("fail_message") or "no reason given").strip()
    if title:
        release_repo.add_review_rejected_title(db, provider, pid, title)
    rows = upgrades._scene_rows(db, provider, pid)
    upgrade_of = next((r.get("upgrade_of") for r in rows if r.get("upgrade_of")), None)
    release_repo.set_download_job_for_scene(db, provider, pid, None, None)
    summary = {"title": title, "reason": reason, "upgrade": bool(upgrade_of)}
    if upgrade_of:
        with db.connect() as conn:
            cand = conn.execute("SELECT * FROM upgrade_candidate WHERE provider_scene_id = ? AND file_path = ?",
                                (pid, upgrade_of)).fetchone()
        if cand is not None:
            cand = dict(cand)
            rejected = upgrades._rejected_titles(cand)
            if title and title not in rejected:
                rejected.append(title)
            # rows that existed before the grab (a watched entity) go back to 'imported'/'new'; only the pseudo row is removed
            previous = [(r["entity_type"], r["entity_stashdb_id"], "imported" if r.get("imported_at") else "new")
                        for r in rows if not str(r["entity_stashdb_id"]).startswith("upgrade:")]
            upgrades._undo_release_rows(db, cand, previous)
            upgrades.set_candidate(db, cand["id"], status="wanted", rejected_titles=json.dumps(rejected),
                                   last_search_result=f"download failed: {reason}"[:200])
            summary["candidate"] = cand["id"]
    history.scene("warning", "download", f"Download failed in {(job or {}).get('client') or 'SABnzbd'}: {title or '?'} -- {reason}"
                  + (" (upgrade: the next upgrade search tries another release)" if upgrade_of
                     else " (back on the wishlist, the next search tries another release)"),
                  provider, pid)
    logger.warning("download failed for scene %s: %s (%s)", pid, title, reason)
    return summary
