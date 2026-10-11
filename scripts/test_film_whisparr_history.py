"""Tests of film_whisparr.poll_history / searched_by_whisparr / film_search.due_films against a THROWAWAY database and a FAKE Whisparr
(stage C, v0.159.0). Run:  venv/bin/python scripts/test_film_whisparr_history.py   -- never touches the live database or any service."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()

import download_client_repo  # noqa: E402
import film_repo  # noqa: E402
import film_search  # noqa: E402
import film_whisparr  # noqa: E402
from db import Database  # noqa: E402


class FakeWhisparr:
    def __init__(self, events, movies):
        self.events, self.movies, self.calls = events, movies, []

    def history_since(self, date_iso):
        self.calls.append(("history_since", date_iso))
        return self.events

    def get_movie(self, movie_id):
        self.calls.append(("get_movie", movie_id))
        return self.movies[movie_id]


class FakeStash:
    def __init__(self): self.scanned = []
    def scan_paths(self, paths): self.scanned.append(paths)


def make_db():
    return Database(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))


def add_film(db, title, whisparr_id, status="wanted"):
    fid, _ = film_repo.add(db, title=title, year=2020, tpdb_id=f"tpdb-{title}")
    film_repo.update(db, fid, status=status, whisparr_id=whisparr_id, whisparr_state="registered")
    return fid


FAILS = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


NOW = datetime(2026, 10, 7, 20, 0, 0, tzinfo=timezone.utc)
ev = lambda kind, movie, minutes, **kw: {"eventType": kind, "movieId": movie, "date": (NOW + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"), **kw}  # noqa: E731

# 1. wrong preset: nothing happens
db = make_db()
add_film(db, "A", 11)
fw = FakeWhisparr([], {})
r = film_whisparr.poll_history(db, fw, FakeStash(), "bookkeeping_only", now=NOW)
check("bookkeeping_only: skipped, no request", "skipped" in r and not fw.calls)

# 2. first run only sets the marker, no events fetched, nothing taken
fw = FakeWhisparr([ev("grabbed", 11, -60, downloadId="SAB_1", sourceTitle="A.2020.1080p")], {})
r = film_whisparr.poll_history(db, fw, FakeStash(), "search_sidecar_import", now=NOW)
check("first run: marker set, no history request", r["first_run"] and not fw.calls and film_whisparr._get_marker(db))
check("first run: the old grab is NOT taken", film_repo.get(db, 1)["status"] == "wanted")

# 3. C1: a grab after the marker is taken (job id + usenet client), repeated event is ignored
download_client_repo.create(db, "sab", "sabnzbd", "usenet", 1, True, "http://sab", "k", "", "", "", None)
later = NOW + timedelta(hours=1)
fw = FakeWhisparr([ev("grabbed", 11, 61, downloadId="SAB_1", sourceTitle="A.2020.1080p"), ev("grabbed", 99, 62, downloadId="X"),
                   ev("downloadFolderImported", 11, 63)], {})
r = film_whisparr.poll_history(db, fw, FakeStash(), "search_sidecar_import", now=later)
f = film_repo.get(db, 1)
check("C1: grab taken over", r["grab"] == ["A"] and f["status"] == "grabbed" and f["download_job_id"] == "SAB_1" and f["grabbed_title"] == "A.2020.1080p")
check("C1: client set", f["download_client_id"] is not None)
check("C1: a foreign movie's event and an import event are ignored (sidecar imports itself)", r["ignored"] == 2 and not r["import"])
check("C1: marker moved to ~now (minus the overlap)", film_whisparr._get_marker(db) < (later.strftime("%Y-%m-%dT%H:%M:%SZ")))
fw = FakeWhisparr([ev("grabbed", 11, 61, downloadId="SAB_1")], {})
r = film_whisparr.poll_history(db, fw, FakeStash(), "search_sidecar_import", now=later + timedelta(hours=1))
check("C1: the same grab seen again changes nothing", not r["grab"] and film_repo.get(db, 1)["download_job_id"] == "SAB_1")

# 4. C1 without usenet client: error, film stays wanted
db2 = make_db(); add_film(db2, "B", 22); film_whisparr._set_marker(db2, "2026-10-07T19:00:00Z")
r = film_whisparr.poll_history(db2, FakeWhisparr([ev("grabbed", 22, 5, downloadId="S")], {}), FakeStash(), "search_sidecar_import", now=NOW)
check("C1 without a usenet client: reported, film stays wanted", len(r["errors"]) == 1 and film_repo.get(db2, 1)["status"] == "wanted")

# 5. C2: grab = status only (no client), import = path + scan + synced
db3 = make_db(); add_film(db3, "C", 33); film_whisparr._set_marker(db3, "2026-10-07T19:00:00Z")
stash = FakeStash()
movies = {33: {"movieFile": {"path": "/data/Movies/C (2020)/C (2020) WEBDL-1080p.mkv"}}}
r = film_whisparr.poll_history(db3, FakeWhisparr([ev("grabbed", 33, 1, downloadId="D", sourceTitle="C.rel"), ev("downloadFolderImported", 33, 30)], movies),
                               stash, "full_whisparr", now=NOW)
f = film_repo.get(db3, 1)
check("C2: grab sets 'grabbed' without a download client", r["grab"] == ["C"] and f["download_client_id"] is None and f["download_job_id"] is None)
check("C2: import records the path and scans the folder", r["import"] == ["C"] and json.loads(f["pending_import_path"]) == ["/data/Movies/C (2020)/C (2020) WEBDL-1080p.mkv"]
      and stash.scanned == [["/data/Movies/C (2020)"]])
check("C2: stage B state is 'synced' (Whisparr knows the file)", f["whisparr_file_state"] == "synced")

# 6. C2: an import event without a file is reported, nothing changes
db4 = make_db(); add_film(db4, "D", 44); film_whisparr._set_marker(db4, "2026-10-07T19:00:00Z")
r = film_whisparr.poll_history(db4, FakeWhisparr([ev("downloadFolderImported", 44, 1)], {44: {"movieFile": None}}), FakeStash(), "full_whisparr", now=NOW)
check("C2: import event without a file -> error, no change", len(r["errors"]) == 1 and film_repo.get(db4, 1)["pending_import_path"] is None)

# 7. dry run writes nothing (not even the marker) and looks back without one
db5 = make_db(); add_film(db5, "E", 55)
fw = FakeWhisparr([ev("grabbed", 55, -600, downloadId="Z", sourceTitle="E.rel")], {})
download_client_repo.create(db5, "sab", "sabnzbd", "usenet", 1, True, "http://sab", "k", "", "", "", None)
r = film_whisparr.poll_history(db5, fw, FakeStash(), "search_sidecar_import", dry_run=True, now=NOW)
check("dry run: reports the grab, writes nothing, sets no marker", r["grab"] == ["E"] and film_repo.get(db5, 1)["status"] == "wanted" and film_whisparr._get_marker(db5) is None)
check("dry run without marker looks 7 days back", fw.calls[0][1] == (NOW - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"))

# 8. the sidecar's own movie search skips movies Whisparr searches (only where Whisparr searches); a manual search is always allowed
db6 = make_db(); a = add_film(db6, "F", 66); b, _ = film_repo.add(db6, title="G", year=2020, tpdb_id="tpdb-G"); film_repo.update(db6, b, status="wanted")
import routers.settings as rs  # noqa: E402
with db6.connect() as conn:
    conn.execute("INSERT INTO setting (key, value) VALUES ('whisparr_url', 'http://w'), ('whisparr_api_key', 'k')")
    conn.execute("INSERT INTO setting (key, value) VALUES ('whisparr_scope_preset', 'search_sidecar_import')")
due = {f["title"] for f in film_search.due_films(db6, per_run=10)}
check("search_sidecar_import: Whisparr's movie is skipped, the one without a Whisparr entry is searched", due == {"G"})
check("manual search of a given movie still allowed", {f["title"] for f in film_search.due_films(db6, only_ids={a})} == {"F"})
with db6.connect() as conn:
    conn.execute("UPDATE setting SET value='bookkeeping_only' WHERE key='whisparr_scope_preset'")
check("bookkeeping_only: both movies are searched by the sidecar", {f["title"] for f in film_search.due_films(db6, per_run=10)} == {"F", "G"})

print("\n%s" % ("ALL OK" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
