"""Recycle bin for a file file_importer.py is about to replace.

Added 2026-09-19 as a direct response to that same day's data-loss
bug (see PROGRESS.md's "Milestone 2 cutover: RESOLVED" entry): a
duplicate SabnzbdSource item silently overwrote a just-imported real
955 MB video with a leftover 31 MB sample, with nothing recoverable
afterward. `file_importer._recycle_existing_target()` now moves
whatever's already at a target path here instead of letting
`_move_file()`'s `shutil.copy2()`/`os.link()` fallback silently destroy
it -- this module is the other half: periodically purging entries once
they're old enough that they're clearly not needed anymore, so the bin
doesn't grow forever.

Lives inside the library mount (the user's own choice, 2026-09-19)
rather than a separately-configured path -- no extra mount needed, and
a hardlink between the original location and the recycle bin stays
possible (same filesystem), matching how Whisparr's own Recycling Bin
setting is typically pointed at a folder on the same share.
"""
import logging
import os
import shutil
import time

logger = logging.getLogger(__name__)


def recycle_bin_root(local_media_mount, recycle_bin_path):
    return os.path.join(local_media_mount.rstrip("/"), recycle_bin_path.strip("/"))


def cleanup_recycle_bin(local_media_mount, recycle_bin_path, max_age_days):
    """Permanently deletes any recycled file older than `max_age_days`
    (by mtime, set at the moment it was recycled -- see
    file_importer._recycle_existing_target()), plus any subfolder left
    empty as a result. Returns {"checked", "deleted": [...], "errors": [...]}.
    Best-effort per file, same reasoning as file_importer.py's own
    cleanup helpers -- one file this process can't remove shouldn't
    stop the rest of the sweep."""
    root = recycle_bin_root(local_media_mount, recycle_bin_path)
    if not os.path.isdir(root):
        return {"checked": 0, "deleted": [], "errors": []}

    cutoff = time.time() - (max_age_days * 86400)
    checked, deleted, errors = 0, [], []

    for dirpath, _dirs, files in os.walk(root, topdown=False):
        for name in files:
            full = os.path.join(dirpath, name)
            checked += 1
            try:
                # ctime changes when a file is MOVED into the bin, mtime does not (2026-09-29: files
                # recycled with old mtimes were purged at the next start, not after the retention).
                st = os.stat(full)
                if max(st.st_mtime, st.st_ctime) < cutoff:
                    os.remove(full)
                    deleted.append(full)
            except OSError as e:
                logger.warning("cleanup_recycle_bin: couldn't remove %s: %s", full, e)
                errors.append({"path": full, "error": str(e)})
        if dirpath != root:
            try:
                st = os.stat(dirpath)
                if max(st.st_mtime, st.st_ctime) < cutoff:  # an empty folder recycled today stays its retention too
                    os.rmdir(dirpath)
            except OSError:
                pass  # not empty (still has files younger than the cutoff) -- leave it

    if deleted:
        logger.info("recycle bin cleanup: permanently deleted %d file(s) older than %d day(s)",
                    len(deleted), max_age_days)
    return {"checked": checked, "deleted": deleted, "errors": errors}
