"""Export / import of the user's own data (v0.173.0) -- for moving to another machine, a fresh install, or a backup you can read.

What goes into an export is decided here and nowhere else:
  * the user's work: watchlist + scan state, releases, the movies wishlist, decisions (Review marks, aliases, compare pairs), settings that are
    preferences, quality profiles, optionally the check results and the history log;
  * NEVER: URLs, API keys, passwords, tokens, local paths (the connection tables indexer / download_client / metadata_provider and every setting
    whose name says so). A new installation gets those entered like a new user would.
The file is a ZIP: manifest.json, tables/<table>.jsonl (one JSON object per row), files/... (saved covers of deleted groups).

Import goes into a FRESH installation only (watchlist, releases and the movies wishlist must be empty), after a dry run that only counts,
and after a backup of the database. Downloads that were running are not carried (their job ids belong to the old download client).
"""
import base64
import io
import json
import os
import re
import sqlite3
import time
import zipfile

import history
from version import SIDECAR_VERSION

FORMAT = 1
SEED_IMAGES = "movie_groups_deleted_20261004_images"  # saved covers of deleted groups (see routers/films.py)

# key -> (label, tables, on by default). The table 'setting' is exported filtered (see _setting_allowed).
GROUPS = [
    ("watchlist", "Watchlist and scan state", ["watchlist", "watchlist_activity", "watermark", "backlog_loaded_year", "backlog_overview",
                                               "muted_studio", "pending_favorite_decision"], True),
    ("releases", "Releases (new, dismissed, imported, on the wishlist)", ["release"], True),
    ("movies", "Movies wishlist (with the saved covers of deleted groups)", ["film_want"], True),
    ("decisions", "Your decisions (Review marks, aliases, compare pairs)", ["review_mark", "scene_alias", "compare_pair", "inactive_review"], True),
    ("settings", "Settings that are preferences (intervals, filters, scope; no URLs, keys, passwords or paths) and quality profiles",
     ["setting", "quality_profile"], True),
    ("checks", "Check results (VR, upgrade candidates, fingerprints, library check) -- saves asking again", ["upgrade_candidate", "vr_file_check",
                                                                                                           "fingerprint_check", "library_check_run",
                                                                                                           "library_check_finding"], True),
    ("history", "History log (only on request)", ["history"], False),
]
GROUP_KEYS = [g[0] for g in GROUPS]
TABLES = {t for g in GROUPS for t in g[2]}
# Never exported, whatever the user ticks (connection data).
NEVER = ("indexer", "download_client", "metadata_provider")
# Core tables that must be empty for an import (a fresh installation).
FRESH_TABLES = ("watchlist", "release", "film_want")
# Columns that point into the OLD download client; set to NULL on import (a running download does not move).
RESET_COLUMNS = {"release": ("download_client_id", "download_job_id"), "film_want": ("download_client_id", "download_job_id")}
# Tables that are emptied before their rows come in (seeded defaults would clash); 'setting' is merged key by key, 'history' is appended.
MERGE = ("setting",)
APPEND = ("history",)

_SECRET_WORDS = ("api_key", "apikey", "password", "token", "secret", "username", "_url", "path", "import_sources", "mount", "root")


def _setting_allowed(key):
    k = key.lower()
    return not any(w in k for w in _SECRET_WORDS)


def exports_dir(data_dir):
    d = os.path.join(data_dir, "exports")
    os.makedirs(d, exist_ok=True)
    return d


def imports_dir(data_dir):
    d = os.path.join(data_dir, "imports")
    os.makedirs(d, exist_ok=True)
    return d


def _columns(conn, table):
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


# A pending search result (Activity > Needs review) carries the indexer's download link WITH the Prowlarr / indexer key in it: not exported at all
# (after the import the movie / release is simply searched again). Found by tests/test_data_transfer.py on 2026-10-08, before the first export.
EXPORT_NULL = {"release": ("review_candidate",), "film_want": ("review_candidate",)}
_KEY_PARAM = re.compile(r"(?i)\b(apikey|api_key|api-key|token|passkey|rsskey|password)=([^&\"\\\s]+)")


def _scrub(v):
    """Defense in depth: a key / token / password in a query string inside any text is replaced."""
    return _KEY_PARAM.sub(lambda m: f"{m.group(1)}=REMOVED", v) if isinstance(v, str) else v


def _json_value(v):
    return {"__b64__": base64.b64encode(v).decode()} if isinstance(v, (bytes, bytearray)) else _scrub(v)


def _from_json(v):
    return base64.b64decode(v["__b64__"]) if isinstance(v, dict) and "__b64__" in v else v


# ---------- what is there ----------

def group_counts(db):
    """[{key, label, default, tables: {name: rows}}] -- live row counts (the 'setting' count is the exportable keys only)."""
    out = []
    with db.connect() as conn:
        for key, label, tables, default in GROUPS:
            counts = {}
            for t in tables:
                if t == "setting":
                    counts[t] = sum(1 for r in conn.execute("SELECT key FROM setting") if _setting_allowed(r["key"]))
                else:
                    counts[t] = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            out.append({"key": key, "label": label, "default": default, "tables": counts})
    return out


def list_files(data_dir):
    """The ZIP files in data/exports and data/imports with what their manifest says."""
    out = []
    for folder, d in (("exports", exports_dir(data_dir)), ("imports", imports_dir(data_dir))):
        for name in sorted(os.listdir(d), reverse=True):
            if not name.endswith(".zip"):
                continue
            path = os.path.join(d, name)
            entry = {"name": f"{folder}/{name}", "size": os.path.getsize(path), "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))}
            try:
                m = _read_manifest(path)
                entry.update(version=m.get("version"), created_at=m.get("created_at"), groups=m.get("groups"),
                             rows=sum(t["rows"] for t in m.get("tables", {}).values()))
            except Exception as e:  # noqa: BLE001
                entry["error"] = str(e)
            out.append(entry)
    return out


def resolve(data_dir, name):
    """'exports/x.zip' or 'imports/x.zip' -> absolute path; anything else is refused (no path tricks)."""
    folder, _, base = (name or "").partition("/")
    if folder not in ("exports", "imports") or not base or "/" in base or "\\" in base or base.startswith(".") or not base.endswith(".zip"):
        raise ValueError("unknown file")
    path = os.path.join(exports_dir(data_dir) if folder == "exports" else imports_dir(data_dir), base)
    if not os.path.isfile(path):
        raise ValueError("file not found")
    return path


# ---------- export ----------

def export(db, data_dir, groups):
    groups = [g for g in groups if g in GROUP_KEYS]
    if not groups:
        return {"error": "tick at least one group"}
    tables = [t for key, _, ts, _ in GROUPS if key in groups for t in ts]
    assert not set(tables) & set(NEVER)
    name = f"srm-export-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    path = os.path.join(exports_dir(data_dir), name)
    manifest = {"format": FORMAT, "app": "StashReleaseManager", "version": SIDECAR_VERSION, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "groups": groups, "tables": {}, "files": [], "skipped_settings": 0}
    buf = io.BytesIO()
    with db.connect() as conn, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        conn.execute("BEGIN")  # one consistent snapshot
        for t in tables:
            cols = _columns(conn, t)
            lines = []
            for r in conn.execute(f'SELECT * FROM "{t}"'):
                row = {c: (None if c in EXPORT_NULL.get(t, ()) else _json_value(r[c])) for c in cols}
                if t == "setting":
                    if not _setting_allowed(row["key"]):
                        manifest["skipped_settings"] += 1
                        continue
                lines.append(json.dumps(row, ensure_ascii=False))
            z.writestr(f"tables/{t}.jsonl", "\n".join(lines) + ("\n" if lines else ""))
            manifest["tables"][t] = {"rows": len(lines), "columns": cols}
        if "movies" in groups:
            src = os.path.join(data_dir, SEED_IMAGES)
            if os.path.isdir(src):
                for fn in sorted(os.listdir(src)):
                    fp = os.path.join(src, fn)
                    if os.path.isfile(fp):
                        z.write(fp, f"files/{SEED_IMAGES}/{fn}")
                        manifest["files"].append(f"{SEED_IMAGES}/{fn}")
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    with open(path, "wb") as fh:
        fh.write(buf.getvalue())
    history.event("info", "system", f"Data exported: {name}", details={"groups": groups, "rows": {t: v["rows"] for t, v in manifest["tables"].items()}})
    return {"ok": True, "name": f"exports/{name}", "size": os.path.getsize(path), "groups": groups,
            "rows": {t: v["rows"] for t, v in manifest["tables"].items()}, "skipped_settings": manifest["skipped_settings"]}


# ---------- import ----------

def _read_manifest(path):
    with zipfile.ZipFile(path) as z:
        m = json.loads(z.read("manifest.json"))
    if m.get("app") != "StashReleaseManager" or m.get("format") != FORMAT:
        raise ValueError(f"not an export of this program (format {m.get('format')!r})")
    return m


def _read_rows(z, table):
    raw = z.read(f"tables/{table}.jsonl").decode("utf-8")
    return [json.loads(line) for line in raw.split("\n") if line.strip()]  # (NOT splitlines(): it also cuts at U+2028 etc. that may sit inside a text)


def _plan(db, path, groups=None):
    """Reads the file and the target; returns the plan (counts, warnings, blockers) without changing anything."""
    m = _read_manifest(path)
    in_file = [g for g in m["groups"] if g in GROUP_KEYS]
    use = [g for g in (groups if groups is not None else in_file) if g in in_file]
    plan = {"version": m.get("version"), "created_at": m.get("created_at"), "groups": use, "available_groups": in_file, "tables": {}, "warnings": [],
            "blockers": [], "reset_downloads": 0}
    with db.connect() as conn, zipfile.ZipFile(path) as z:
        for key, _, tables, _ in GROUPS:
            if key not in use:
                continue
            for t in tables:
                if t in NEVER or t not in m["tables"]:
                    continue
                target_cols = _columns(conn, t)
                if not target_cols:
                    plan["blockers"].append(f"the table {t} does not exist in this installation")
                    continue
                src_cols = m["tables"][t]["columns"]
                dropped = [c for c in src_cols if c not in target_cols]
                missing = []
                for r in conn.execute(f'PRAGMA table_info("{t}")'):
                    if r[1] not in src_cols and r[3] and r[4] is None and not r[5]:  # NOT NULL, no default, not the primary key
                        missing.append(r[1])
                if missing:
                    plan["blockers"].append(f"{t}: this installation needs the column(s) {', '.join(missing)} that the file does not have")
                if dropped:
                    plan["warnings"].append(f"{t}: the column(s) {', '.join(dropped)} do not exist here and are left out")
                rows = _read_rows(z, t)
                if t == "setting":
                    rows = [r for r in rows if _setting_allowed(r["key"])]
                plan["tables"][t] = len(rows)
                cols_reset = RESET_COLUMNS.get(t, ())
                plan["reset_downloads"] += sum(1 for r in rows if any(r.get(col) not in (None, "") for col in cols_reset))
        if m.get("tables", {}).get("release", {}).get("rows") is not None and "release" not in plan["tables"] and "releases" in use:
            plan["blockers"].append("the file has no release table")
        if any(g not in ("settings", "history") for g in use):  # everything but settings / history replaces the user's work: only into a fresh installation
            for t in FRESH_TABLES:
                n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                if n:
                    plan["blockers"].append(f"this installation is not empty: {t} has {n} row(s). An import only goes into a fresh installation")
    if plan["reset_downloads"]:
        plan["warnings"].append(f"{plan['reset_downloads']} entr(y/ies) had a download job: it is not carried over (the job belongs to the old download client), "
                                "the release / movie is simply searched again if it still waits")
    return plan


def check(db, data_dir, name, groups=None):
    """The dry run: what would be imported, what would be left out, whether it is allowed. Changes nothing."""
    try:
        path = resolve(data_dir, name)
        return {"ok": True, **_plan(db, path, groups)}
    except (ValueError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as e:
        return {"error": str(e)}


def apply(db, data_dir, name, groups=None):
    try:
        path = resolve(data_dir, name)
        plan = _plan(db, path, groups)
    except (ValueError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as e:
        return {"error": str(e)}
    if plan["blockers"]:
        return {"error": "; ".join(plan["blockers"]), "blockers": plan["blockers"]}
    if not plan["tables"]:
        return {"error": "nothing to import"}
    backup = os.path.join(data_dir, "backups", f"release_watcher.before-import-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3")
    os.makedirs(os.path.dirname(backup), exist_ok=True)
    src = sqlite3.connect(db.path)
    try:
        dst = sqlite3.connect(backup)
        src.backup(dst)
        dst.close()
    finally:
        src.close()
    if not os.path.getsize(backup):
        return {"error": "the backup of the database could not be written -- nothing was imported"}
    conn = sqlite3.connect(db.path, timeout=60)
    conn.isolation_level = None
    done = {}
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        with zipfile.ZipFile(path) as z:
            m = _read_manifest(path)
            for key, _, tables, _ in GROUPS:
                if key not in plan["groups"]:
                    continue
                for t in tables:
                    if t not in plan["tables"]:
                        continue
                    target_cols = _columns(conn, t)
                    rows = _read_rows(z, t)
                    if t == "setting":
                        rows = [r for r in rows if _setting_allowed(r["key"])]
                    if t not in MERGE and t not in APPEND:
                        conn.execute(f'DELETE FROM "{t}"')
                    cols = [c for c in m["tables"][t]["columns"] if c in target_cols and not (t in APPEND and c == "id")]
                    sql = (f'INSERT OR REPLACE INTO "{t}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) VALUES ({", ".join("?" * len(cols))})')
                    for r in rows:
                        vals = [None if c in RESET_COLUMNS.get(t, ()) else _from_json(r.get(c)) for c in cols]
                        conn.execute(sql, vals)
                    done[t] = len(rows)
            bad = conn.execute("PRAGMA foreign_key_check").fetchall()
            if bad:
                raise ValueError(f"{len(bad)} row(s) point at rows that are not there (foreign key check)")
            for t, n in done.items():  # the rows must really be in
                got = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                if t not in MERGE and t not in APPEND and got != n:
                    raise ValueError(f"{t}: {n} row(s) read, {got} in the table")
            conn.execute("COMMIT")
            copied = 0
            if "movies" in plan["groups"]:
                dest = os.path.join(data_dir, SEED_IMAGES)
                for fn in m.get("files", []):
                    if fn.startswith(SEED_IMAGES + "/") and "/" not in fn[len(SEED_IMAGES) + 1:] and ".." not in fn:
                        os.makedirs(dest, exist_ok=True)
                        with open(os.path.join(dest, os.path.basename(fn)), "wb") as fh:
                            fh.write(z.read(f"files/{fn}"))
                        copied += 1
    except Exception as e:  # noqa: BLE001
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        return {"error": f"{type(e).__name__}: {e} -- nothing was imported (the database is unchanged)", "backup": os.path.basename(backup)}
    finally:
        conn.close()
    history.event("info", "system", f"Data imported from {name}", details={"rows": done, "backup": os.path.basename(backup), "warnings": plan["warnings"]})
    return {"ok": True, "rows": done, "files": copied, "warnings": plan["warnings"], "backup": os.path.basename(backup),
            "note": "Restart the container once so that every job picks up the imported settings."}


# ---------- upload of an export file from the browser (v0.180.0) ----------

MAX_UPLOAD_BYTES = 2 * 1024 ** 3


def upload_names(data_dir, name):
    """(temporary path, final path, name as shown) for an uploaded export: only letters, digits, dot, dash, underscore; must end in .zip; never
    replaces an existing file (a counter is added)."""
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(name or "")).strip("._") or "export.zip"
    if not base.lower().endswith(".zip"):
        raise ValueError("only .zip files (an export of this program) can be uploaded")
    d = imports_dir(data_dir)
    stem, ext = base[:-4], ".zip"
    final, n = os.path.join(d, base), 1
    while os.path.exists(final):
        n += 1
        final = os.path.join(d, f"{stem}-{n}{ext}")
    return final + ".part", final, f"imports/{os.path.basename(final)}"


def finish_upload(tmp_path, final_path, expected_size):
    """Checks the received file (size, a ZIP of this program's export) and moves it into place; deletes it and raises if it is not."""
    try:
        if os.path.getsize(tmp_path) != expected_size:
            raise ValueError("the file arrived incomplete")
        _read_manifest(tmp_path)  # raises for anything that is not an export of this program
        os.replace(tmp_path, final_path)
    except (ValueError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise ValueError(str(e) if isinstance(e, ValueError) else "this is not an export file of this program") from None
