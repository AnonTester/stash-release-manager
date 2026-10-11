"""Whisparr bookkeeping of the movie wishlist (v0.140.0, stage A) -- the movies follow the SAME single `whisparr_scope_preset` as the scenes.

Whisparr has a movie area of its own: 29k entries of itemType 'movie' whose `foreignId` is `tpdbId:<ThePornDB uuid>` -- the very id of film_want.tpdb_id
(checked live 2026-10-06; a /movie/lookup by TITLE returns them, a lookup by an id term does not). Whisparr links ONE file per movie (the first
scene); the rest of the folder is ignored.

What a preset means for a movie (abstracted from the scenes, see handoff.py / PROGRESS.md "WHISPARR RULES"):
- no_whisparr (or Whisparr not configured): Whisparr is not touched at all.
- bookkeeping_only: the movie is registered in Whisparr as bookkeeping (monitored=False, no search); the sidecar searches, downloads and imports.
- search_sidecar_import / full_whisparr: the movie is registered monitored and Whisparr searches for THIS movie (request_download). Monitoring of
  studios/performers always stays with the sidecar (the safety sweep keeps those unmonitored) -- only individual movies are ever monitored.
A movie that is already imported (has its file) is never monitored. Stage B (v0.158.0, `sync_files()` below: the folder path + rescan after an import) is
the same way library_organize.sync_whisparr() tells Whisparr about a moved scene file; C (taking Whisparr's grab into the film import) follows;
duplicates (D) are decided by quality.

`run()` registers/links the movies of the wishlist (wanted, grabbed, imported, import failed) that have no whisparr_id yet: automatically only the
ones that just came onto the wishlist ('queued'), with `all_movies` (the manual catch-up) also the older ones; `dry_run=True` only reports what it
would do (one lookup request per movie, nothing is written)."""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import download_client_repo
import film_repo
import history
from whisparr_client import MovieAlreadyExistsError

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("wanted", "grabbed", "imported", "import_failed")
RETRY_DAYS = 1  # a movie Whisparr did not know / an error is looked up again after this many days


def wants_monitored(scope, film):
    """Whisparr searches for this movie itself only under the presets where Whisparr searches, and never for one that is already imported."""
    return scope in ("full_whisparr", "search_sidecar_import") and film["status"] != "imported"


def lookup(client, film):
    """The Whisparr lookup resource of this movie (matched on foreignId == tpdbId:<our id>), else None. One request."""
    term = f"{film['title']} {film.get('year') or ''}".strip()
    resp = client.session.get(f"{client.base}/movie/lookup", params={"term": term}, timeout=client.timeout)
    resp.raise_for_status()
    wanted = f"tpdbId:{film['tpdb_id']}"
    for item in resp.json():
        if str(item.get("foreignId")) == wanted:
            return item
    return None


def _due(db, film, all_movies=False):
    if film.get("whisparr_id"):
        return False
    if all_movies and film.get("whisparr_state") == "conflict":
        return True  # the manual catch-up looks at a conflict again at once (stage D: the rule that classifies it may have changed)
    last = film.get("whisparr_checked_at")
    if not last or film.get("whisparr_state") in (None, "", "queued"):
        return True
    with db.connect() as conn:
        age = conn.execute("SELECT julianday('now') - julianday(?) AS d", (last,)).fetchone()["d"]
    return age is None or age >= RETRY_DAYS


def queue(db, film_ids):
    """Marks movies that just came onto the wishlist (accepted suggestion, added movie) as 'queued': only those are told to Whisparr automatically.
    The movies that were on the wishlist before this feature are NOT touched automatically -- they are caught up by hand (Maintenance > Movies &
    groups: dry run, then "Sync now")."""
    for fid in film_ids:
        film = film_repo.get(db, fid)
        if film and not film.get("whisparr_id") and film.get("tpdb_id") and film.get("whisparr_state") in (None, "", "not_found", "error"):
            film_repo.update(db, fid, whisparr_state="queued", whisparr_checked_at=None)


def pending(db, all_movies=False):
    """The movies to tell Whisparr about. Automatically only the 'queued' ones (+ failed ones due for a retry); `all_movies` (the manual catch-up) also
    the older ones nobody has looked at yet."""
    rows = film_repo.list_films(db, list(ACTIVE_STATUSES), per_page=0)["rows"]
    out = []
    for f in rows:
        if not f.get("tpdb_id") or not _due(db, f, all_movies):
            continue
        if all_movies or f.get("whisparr_state") in ("queued", "error", "not_found"):  # (the manual catch-up also looks at 'conflict' again)
            out.append(f)
    return out


RUNTIME_TOLERANCE = 0.25


def _entry_minutes(entry):
    """Runtime of a Whisparr entry in minutes: its own `runtime`, else the length of its file ('h:mm:ss'), else None."""
    if entry.get("runtime"):
        return float(entry["runtime"])
    run_time = ((entry.get("movieFile") or {}).get("mediaInfo") or {}).get("runTime")
    if run_time:
        try:
            parts = [float(x) for x in str(run_time).split(":")]
            return sum(v * 60 ** i for i, v in enumerate(reversed(parts))) / 60
        except ValueError:
            return None
    return None


def find_duplicate_entry(client, film):
    """Stage D: Whisparr has THIS movie already under ANOTHER ThePornDB id (same title and year). Returns None (no such entry), ("adopt", entry, note) --
    exactly one such entry whose length fits (or is unknown) -- or ("conflict", None, note) -- several entries or a length that does not fit.
    One request for the whole movie list at most (cached for 10 minutes)."""
    ours = f"tpdbId:{film.get('tpdb_id')}"
    others = [m for m in client.movies_by_title_year(film["title"], film.get("year")) if str(m.get("foreignId")) != ours]
    if not others:
        return None
    if len(others) > 1:
        return "conflict", None, f"{len(others)} Whisparr entries have this title and year ({', '.join(str(m['id']) for m in others[:5])}) -- decide by hand"
    entry = others[0]
    expected, minutes = film.get("duration_min"), _entry_minutes(entry)
    if expected and minutes and abs(minutes / expected - 1) > RUNTIME_TOLERANCE:
        return "conflict", None, (f"Whisparr entry {entry['id']} has this title and year but {minutes:.0f} min, ThePornDB says {expected:.0f} min "
                                  "-- possibly another movie, decide by hand")
    return "adopt", entry, f"Whisparr entry {entry['id']} ({entry.get('foreignId')}) is the same movie (title and year)"


def run(db, client, scope, profile_id, root_folder, dry_run=False, pause=0.25, all_movies=False):
    """Returns {"preset", "dry_run", "checked", "register": [...], "link": [...], "set_monitored": [...], "not_found": [...], "no_tpdb": n, "errors": [...]}
    (the lists hold movie titles). With dry_run nothing is written (neither in Whisparr nor in the database)."""
    out = {"preset": scope, "dry_run": dry_run, "checked": 0, "register": [], "link": [], "set_monitored": [], "not_found": [], "no_tpdb": 0, "errors": [],
           "adopt": [], "conflict": []}
    if scope == "no_whisparr":
        out["skipped"] = "preset no_whisparr: Whisparr is not touched"
        return out
    out["no_tpdb"] = sum(1 for f in film_repo.list_films(db, list(ACTIVE_STATUSES), per_page=0)["rows"] if not f.get("tpdb_id") and not f.get("whisparr_id"))
    for film in pending(db, all_movies=all_movies):
        out["checked"] += 1
        want = wants_monitored(scope, film)
        try:
            item = lookup(client, film)
            if item is None:
                out["not_found"].append(film["title"])
                if not dry_run:
                    film_repo.update(db, film["id"], whisparr_state="not_found", whisparr_checked_at=film_repo.now())
            elif item.get("id"):  # Whisparr has it already (it carries an id in the lookup)
                out["link"].append(film["title"])
                current = bool(item.get("monitored")) if "monitored" in item else bool(client.get_movie(item["id"]).get("monitored"))
                if current != want:
                    out["set_monitored"].append(film["title"])
                if not dry_run:
                    if current != want:
                        client.set_movie_monitored(item["id"], want)
                    film_repo.update(db, film["id"], whisparr_id=item["id"], whisparr_state="linked", whisparr_checked_at=film_repo.now())
            else:
                dup = find_duplicate_entry(client, film)
                if dup is not None:  # stage D: Whisparr already has THIS movie under another ThePornDB id -- use that entry or report the conflict
                    verdict, entry, note = dup
                    if verdict == "adopt":
                        out["adopt"].append(f"{film['title']} -- {note}")
                        if not dry_run:
                            if bool(entry.get("monitored")) != want:
                                client.set_movie_monitored(entry["id"], want)
                            film_repo.update(db, film["id"], whisparr_id=entry["id"], whisparr_state="adopted", whisparr_note=note, whisparr_checked_at=film_repo.now())
                    else:
                        out["conflict"].append(f"{film['title']} -- {note}")
                        if not dry_run:
                            film_repo.update(db, film["id"], whisparr_state="conflict", whisparr_note=note, whisparr_checked_at=film_repo.now())
                    time.sleep(pause)
                    continue
                out["register"].append(film["title"])
                if not dry_run:
                    register = client.request_download if want else client.register_existing
                    try:
                        movie = register(item, profile_id, root_folder)
                    except MovieAlreadyExistsError:
                        again = lookup(client, film)
                        movie = again if again and again.get("id") else None
                        if movie is None:
                            raise
                    film_repo.update(db, film["id"], whisparr_id=movie["id"], whisparr_state="registered", whisparr_checked_at=film_repo.now())
        except Exception as e:  # noqa: BLE001
            logger.exception("film whisparr sync: %s failed", film["title"])
            out["errors"].append(f"{film['title']}: {type(e).__name__}: {str(e)[:120]}")
            if not dry_run:
                film_repo.update(db, film["id"], whisparr_state="error", whisparr_checked_at=film_repo.now())
        time.sleep(pause)
    if not dry_run and (out["register"] or out["link"] or out["errors"]):
        history.event("warning" if out["errors"] else "info", "whisparr",
                      f"Movies synced to Whisparr ({scope}): {len(out['register'])} registered, {len(out['link'])} linked, {len(out['set_monitored'])} monitoring changed"
                      + (f", {len(out['errors'])} failed" if out["errors"] else ""),
                      details={k: out[k][:20] for k in ("register", "link", "set_monitored", "not_found", "errors")})
    return out


# ---------------------------------------------------------------------------------------------------------------------------------------
# Stage B (v0.158.0): tell Whisparr WHERE an imported movie lies and let it take the file over. Nothing is moved, renamed or deleted.
# Only under the presets where this sidecar imports (bookkeeping_only, search_sidecar_import); with full_whisparr Whisparr imports itself (stage C).
# ---------------------------------------------------------------------------------------------------------------------------------------

FILE_SCOPES = ("bookkeeping_only", "search_sidecar_import")


def queue_file(db, film_id):
    """An import just finished: the movie is to be reported to Whisparr (automatically, once the setting for it is on)."""
    film = film_repo.get(db, film_id)
    if film and film.get("whisparr_file_state") not in ("synced", "conflict"):
        film_repo.update(db, film_id, whisparr_file_state="queued", whisparr_file_checked_at=None)


def _file_due(db, film, all_movies):
    state = film.get("whisparr_file_state")
    if state in ("synced", "conflict"):
        return False
    if state == "queued":
        return True
    if state in (None, "", "not_taken"):
        return all_movies  # an older import nobody has looked at / Whisparr did not take the file (e.g. a movie in parts): only the manual catch-up looks again
    last = film.get("whisparr_file_checked_at")  # error / not_taken: tried again after a day
    if not last:
        return True
    with db.connect() as conn:
        age = conn.execute("SELECT julianday('now') - julianday(?) AS d", (last,)).fetchone()["d"]
    return age is None or age >= RETRY_DAYS


def pending_files(db, all_movies=False):
    rows = film_repo.list_films(db, ["imported"], per_page=0)["rows"]
    return [f for f in rows if f.get("whisparr_id") and _file_due(db, f, all_movies)]


def film_folder(stash, film):
    """The folder the movie's file(s) lie in (the Stash path, which is also Whisparr's path), from its Stash group: (folder, [file names]) or (None, reason)."""
    gid = film.get("imported_group_id")
    if not gid:
        return None, "no Stash group"
    group = stash._graphql("query($id: ID!) { findGroup(id: $id) { scenes { files { path } } } }", {"id": str(gid)})["findGroup"]
    paths = [f["path"] for sc in (group or {}).get("scenes", []) for f in sc["files"]]
    folders = {os.path.dirname(p) for p in paths}
    if len(folders) != 1:
        return None, ("the group has no file" if not folders else f"the group's files lie in {len(folders)} folders")
    return folders.pop(), sorted(os.path.basename(p) for p in paths)


def sync_files(db, client, stash, scope, dry_run=False, all_movies=False, pause=0.25):
    """Returns {"preset", "dry_run", "checked", "set_path": [...], "already": [...], "conflict": [...], "not_taken": [...], "no_folder": [...],
    "errors": [...]} (lists of 'title' or 'title -- note'). With dry_run nothing is written (neither in Whisparr nor in the database)."""
    out = {"preset": scope, "dry_run": dry_run, "checked": 0, "set_path": [], "already": [], "conflict": [], "not_taken": [], "no_folder": [], "errors": []}
    if scope not in FILE_SCOPES:
        out["skipped"] = f"preset {scope}: the movie folders are not reported to Whisparr" + (" (Whisparr imports itself)" if scope == "full_whisparr" else "")
        return out
    for film in pending_files(db, all_movies=all_movies):
        out["checked"] += 1
        title = film["title"]
        state = None
        try:
            folder, names = film_folder(stash, film)
            if folder is None:
                out["no_folder"].append(f"{title} -- {names}")
                state = "error"
            else:
                movie = client.get_movie(film["whisparr_id"])
                current = (movie.get("path") or "").rstrip("/")
                known = (movie.get("movieFile") or {}).get("relativePath") or ""
                if current == folder.rstrip("/") and known in names:
                    out["already"].append(title)
                    state = "synced"
                else:
                    owner = None
                    if current != folder.rstrip("/"):
                        owner = next((m for m in client.movies_in_folder(folder) if m["id"] != film["whisparr_id"]), None)
                    if owner is not None:
                        out["conflict"].append(f"{title} -- Whisparr has this folder as {owner.get('title')!r}")
                        state = "conflict"
                    elif dry_run:
                        out["set_path"].append(f"{title} -- {current or 'no path'} -> {folder}" if current != folder.rstrip("/") else f"{title} -- rescan only")
                    else:
                        if current != folder.rstrip("/"):
                            client.set_movie_path(film["whisparr_id"], folder)
                        status = client.rescan_movie(film["whisparr_id"])
                        after = (client.get_movie(film["whisparr_id"]).get("movieFile") or {}).get("relativePath") or ""
                        if after in names:
                            out["set_path"].append(title)
                            state = "synced"
                        else:
                            out["not_taken"].append(f"{title} -- rescan {status}, Whisparr knows {after or 'no file'}")
                            state = "not_taken"
        except Exception as e:  # noqa: BLE001
            logger.exception("film whisparr file sync: %s failed", title)
            out["errors"].append(f"{title}: {type(e).__name__}: {str(e)[:120]}")
            state = "error"
        if state and not dry_run:
            film_repo.update(db, film["id"], whisparr_file_state=state, whisparr_file_checked_at=film_repo.now())
        time.sleep(pause)
    if not dry_run and out["checked"]:
        bad = out["conflict"] + out["not_taken"] + out["no_folder"] + out["errors"]
        history.event("warning" if bad else "info", "whisparr",
                      f"Movie folders reported to Whisparr ({scope}): {len(out['set_path'])} done, {len(out['already'])} already right"
                      + (f", {len(out['conflict'])} conflict, {len(out['not_taken'])} not taken, {len(out['no_folder']) + len(out['errors'])} failed" if bad else ""),
                      details={k: out[k][:20] for k in ("set_path", "already", "conflict", "not_taken", "no_folder", "errors")})
    return out


# ---------------------------------------------------------------------------------------------------------------------------------------
# Stage C (v0.159.0): where WHISPARR searches (search_sidecar_import, full_whisparr) the sidecar learns what Whisparr did from ONE history request
# per run (`/history/since`) instead of one per movie:
# - search_sidecar_import: a 'grabbed' event -> the movie is 'grabbed' with Whisparr's SABnzbd job id (exactly what the scene path does with
#   find_grabbed_download_id); the existing film import (film_import.run) takes it from there (SABnzbd -> library -> Stash -> Group).
# - full_whisparr: Whisparr imports itself. A 'downloadFolderImported' event -> the file's path is read from Whisparr (one more request), the folder is
#   scanned in Stash and the existing film_import.finalize waits for the scene, checks the length and builds the Group.
# Whisparr is only READ; this writes to our own database and (as every film import does) asks Stash to scan the folder. The first run only sets the
# marker (no catching up on old events).
# ---------------------------------------------------------------------------------------------------------------------------------------

HISTORY_SCOPES = ("search_sidecar_import", "full_whisparr")
HISTORY_MARKER = "film_whisparr_history_at"
HISTORY_OVERLAP_SECONDS = 120  # events are looked at again for a moment (every step is idempotent) so none is lost at the edge
DRY_RUN_LOOKBACK_DAYS = 7


def _utc_iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_marker(db):
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM setting WHERE key = ?", (HISTORY_MARKER,)).fetchone()
    return row["value"] if row and row["value"] else None


def _set_marker(db, iso):
    with db.connect() as conn:
        conn.execute("INSERT INTO setting (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", (HISTORY_MARKER, iso))


def searched_by_whisparr(db, scope, film):
    """True when Whisparr (not this sidecar) searches for this movie: a preset where Whisparr searches and the movie is registered there."""
    return scope in HISTORY_SCOPES and bool(film.get("whisparr_id"))


def poll_history(db, client, stash, scope, dry_run=False, now=None):
    """Returns {"preset", "dry_run", "first_run", "events", "grab": [...], "import": [...], "ignored", "errors": [...]} (lists of movie titles). With
    dry_run nothing is written (not even the marker); without a marker it looks back DRY_RUN_LOOKBACK_DAYS days."""
    out = {"preset": scope, "dry_run": dry_run, "first_run": False, "events": 0, "grab": [], "import": [], "ignored": 0, "errors": []}
    if scope not in HISTORY_SCOPES:
        out["skipped"] = f"preset {scope}: Whisparr does not search, nothing to take over"
        return out
    now = now or datetime.now(timezone.utc)
    marker = _get_marker(db)
    if not marker and not dry_run:
        _set_marker(db, _utc_iso(now))
        out["first_run"] = True
        out["note"] = "first run: only the starting point was set -- earlier Whisparr events are not taken over"
        return out
    since = marker or _utc_iso(now - timedelta(days=DRY_RUN_LOOKBACK_DAYS))
    events = client.history_since(since)
    out["events"] = len(events)
    films = {f["whisparr_id"]: f for f in film_repo.list_films(db, ["wanted", "grabbed"], per_page=0)["rows"] if f.get("whisparr_id")}
    for e in sorted(events, key=lambda x: x.get("date") or ""):
        film = films.get(e.get("movieId"))
        kind = e.get("eventType")
        if film is None or kind not in ("grabbed", "downloadFolderImported"):
            out["ignored"] += 1
            continue
        title = film["title"]
        try:
            if kind == "grabbed":
                if film["status"] != "wanted":
                    out["ignored"] += 1  # already taken over (a repeated event)
                    continue
                fields = {"status": "grabbed", "grabbed_title": e.get("sourceTitle"), "grabbed_at": film_repo.now(), "import_error": None,
                          "review_candidate": None, "last_search_result": "whisparr_grab"}
                if scope == "search_sidecar_import":
                    row = download_client_repo.pick_for_protocol(db, "usenet")
                    if row is None or not e.get("downloadId"):
                        out["errors"].append(f"{title}: grabbed in Whisparr, but no usenet download client / download id to follow it")
                        continue
                    fields.update(download_client_id=row["id"], download_job_id=e["downloadId"])
                out["grab"].append(title)
                if not dry_run:
                    film_repo.update(db, film["id"], **fields)
                    film["status"] = "grabbed"
            else:  # downloadFolderImported, only meaningful where Whisparr imports itself
                if scope != "full_whisparr" or film.get("pending_import_path"):
                    out["ignored"] += 1
                    continue
                movie = client.get_movie(film["whisparr_id"])
                path = (movie.get("movieFile") or {}).get("path")
                if not path:
                    out["errors"].append(f"{title}: Whisparr reports an import but no file")
                    continue
                out["import"].append(title)
                if not dry_run:
                    film_repo.update(db, film["id"], status="grabbed", pending_import_path=json.dumps([path]), import_error=None,
                                     whisparr_file_state="synced", whisparr_file_checked_at=film_repo.now())
                    film["pending_import_path"] = json.dumps([path])
                    stash.scan_paths([os.path.dirname(path)])
        except Exception as ex:  # noqa: BLE001
            logger.exception("film whisparr history: %s failed", title)
            out["errors"].append(f"{title}: {type(ex).__name__}: {str(ex)[:120]}")
    if not dry_run:
        _set_marker(db, _utc_iso(now - timedelta(seconds=HISTORY_OVERLAP_SECONDS)))
        if out["grab"] or out["import"] or out["errors"]:
            history.event("warning" if out["errors"] else "info", "whisparr",
                          f"Whisparr history taken over for movies ({scope}): {len(out['grab'])} grabbed, {len(out['import'])} imported"
                          + (f", {len(out['errors'])} failed" if out["errors"] else ""),
                          details={k: out[k][:20] for k in ("grab", "import", "errors")})
    return out
