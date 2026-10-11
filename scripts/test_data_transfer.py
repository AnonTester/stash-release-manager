"""Tests of export / import (v0.173.0): data_transfer.py against THROWAWAY databases filled from a copy of the real data (the newest verified backup in
data/); nothing real is touched and no secret value is printed. Run:  venv/bin/python scripts/test_data_transfer.py"""
import glob
import io
import json
import os
import sqlite3
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = tmp

import data_transfer as dt  # noqa: E402
import history  # noqa: E402
from db import Database  # noqa: E402

FAILS = []
def check(name, cond):  # noqa: E302
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: FAILS.append(name)


backups = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..", "data", "release_watcher.pre-session-*.backup.sqlite3")))
if not backups:
    print("no real backup to build test data from -- skipped")
    sys.exit(0)

A = Database(os.path.join(tmp, "a.sqlite3"))
with A.connect() as c:
    c.execute("ATTACH DATABASE ? AS src", (backups[-1],))
    for (t,) in c.execute("SELECT name FROM src.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        c.execute(f'DELETE FROM main."{t}"')
        mine = [r[1] for r in c.execute(f'PRAGMA main.table_info("{t}")')]
        theirs = [r[1] for r in c.execute(f'PRAGMA src.table_info("{t}")')]
        cols = ", ".join(f'"{x}"' for x in mine if x in theirs)  # (a migrated database has its columns in another order)
        c.execute(f'INSERT INTO main."{t}" ({cols}) SELECT {cols} FROM src."{t}"')
history.init(A)
secrets = []
with A.connect() as c:
    secrets += [r[0] for r in c.execute("SELECT value FROM setting WHERE key LIKE '%api_key%' AND value != ''")]
    secrets += [r[0] for r in c.execute("SELECT api_key FROM indexer WHERE api_key != ''")]
    n_dl = c.execute("SELECT COUNT(*) FROM release WHERE download_job_id IS NOT NULL").fetchone()[0] \
        + c.execute("SELECT COUNT(*) FROM film_want WHERE download_job_id IS NOT NULL").fetchone()[0]
env_path = os.path.join(os.path.dirname(__file__), "..", "api", ".env")
if os.path.exists(env_path):  # the keys that only live in .env must not turn up either (they sit in search results)
    for line in open(env_path):
        k, _, v = line.strip().partition("=")
        if v and any(w in k for w in ("KEY", "TOKEN", "PASSWORD")) and len(v) >= 8:
            secrets.append(v)
check("test data: there are secrets to look for", len(secrets) >= 2)

r = dt.export(A, tmp, dt.GROUP_KEYS)
check("export: ok", r.get("ok"))
raw = open(os.path.join(tmp, r["name"]), "rb").read()
check("export: no secret value anywhere in the file (also inside the compressed parts)", not any(s.encode() in raw for s in secrets) and all(
    s.encode() not in zipfile.ZipFile(io.BytesIO(raw)).read(n) for s in secrets for n in zipfile.ZipFile(io.BytesIO(raw)).namelist()))
names = zipfile.ZipFile(io.BytesIO(raw)).namelist()
import re  # noqa: E402
allrows = "\n".join(zipfile.ZipFile(io.BytesIO(raw)).read(n).decode() for n in zipfile.ZipFile(io.BytesIO(raw)).namelist() if n.endswith(".jsonl"))
check("export: no key / token / password parameter with a value in any text", not [m for m in re.findall(r"(?i)\b(?:apikey|api_key|token|passkey|rsskey|password)=(?!REMOVED)[^&\"\\\s]+", allrows)])
check("export: pending search results are not exported", all(json.loads(l).get("review_candidate") is None for l in zipfile.ZipFile(io.BytesIO(raw)).read("tables/release.jsonl").decode().split("\n") if l.strip()))
check("export: the connection tables are not in it", not any(t in n for n in names for t in dt.NEVER))
sets = [json.loads(l)["key"] for l in zipfile.ZipFile(io.BytesIO(raw)).read("tables/setting.jsonl").decode().split("\n") if l.strip()]
check("export: no key / url / path / password setting", not [k for k in sets if not dt._setting_allowed(k)] and "stashdb_api_key" not in sets and "prowlarr_url" not in sets)
check("export: preferences are in (content_scope, intervals, last_run)", "content_scope" in sets and "scan_interval_hours" in sets and any(k.startswith("last_run:") for k in sets))
r_nohist = dt.export(A, tmp, [g for g in dt.GROUP_KEYS if g != "history"])
check("export: without the history group there is no history table", "tables/history.jsonl" not in zipfile.ZipFile(os.path.join(tmp, r_nohist["name"])).namelist())
check("export: unknown group / nothing ticked is refused", "error" in dt.export(A, tmp, []) and "error" in dt.export(A, tmp, ["indexer"]))

B = Database(os.path.join(tmp, "b.sqlite3"))
with B.connect() as c:
    c.execute("INSERT OR REPLACE INTO setting (key, value) VALUES ('stashdb_api_key', 'NEW-INSTALL-KEY')")
chk = dt.check(B, tmp, r["name"])
check("dry run: ok, no blockers, counts match the file", chk.get("ok") and not chk["blockers"] and chk["tables"]["release"] == r["rows"]["release"])
check("dry run: changes nothing", sqlite3.connect(B.path).execute("SELECT COUNT(*) FROM release").fetchone()[0] == 0)
check("dry run: says how many running downloads are not carried", chk["reset_downloads"] == n_dl)

res = dt.apply(B, tmp, r["name"])
check("apply: ok", res.get("ok") and os.path.exists(os.path.join(tmp, "backups", res["backup"])))
with A.connect() as ca, B.connect() as cb:
    same = all(ca.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] == cb.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
               for t in ("watchlist", "release", "film_want", "watermark", "review_mark", "scene_alias", "compare_pair", "quality_profile", "upgrade_candidate",
                         "vr_file_check", "fingerprint_check", "muted_studio"))
    check("apply: the row counts of the tables equal the source", same)
    a = ca.execute("SELECT * FROM release ORDER BY provider, scene_stashdb_id, entity_type, entity_stashdb_id LIMIT 300").fetchall()
    b = cb.execute("SELECT * FROM release ORDER BY provider, scene_stashdb_id, entity_type, entity_stashdb_id LIMIT 300").fetchall()
    cols = [k for k in a[0].keys() if k not in ("download_client_id", "download_job_id")]
    check("apply: release rows are identical (except the download job)", [tuple(r[k] for k in cols) for r in a] == [tuple(r[k] for k in cols) for r in b])
    check("apply: no download job is carried", cb.execute("SELECT COUNT(*) FROM release WHERE download_job_id IS NOT NULL").fetchone()[0] == 0
          and cb.execute("SELECT COUNT(*) FROM film_want WHERE download_job_id IS NOT NULL").fetchone()[0] == 0)
    check("apply: the new installation's own key is kept, the old one did not come", cb.execute("SELECT value FROM setting WHERE key='stashdb_api_key'").fetchone()[0] == "NEW-INSTALL-KEY")
    check("apply: preferences came", cb.execute("SELECT COUNT(*) FROM setting WHERE key='content_scope'").fetchone()[0] == 1)
    check("apply: history was imported (all rows of the file)", cb.execute("SELECT COUNT(*) FROM history").fetchone()[0] >= r["rows"]["history"])
    check("apply: connection tables untouched (empty)", cb.execute("SELECT COUNT(*) FROM indexer").fetchone()[0] == 0)
res2 = dt.apply(B, tmp, r["name"])
check("a second import into the now filled installation is refused", "error" in res2 and "not empty" in res2["error"])

# rollback: a damaged row stops everything, the target stays empty
bad = io.BytesIO()
with zipfile.ZipFile(os.path.join(tmp, r["name"])) as zin, zipfile.ZipFile(bad, "w") as zout:
    for n in zin.namelist():
        d = zin.read(n)
        if n == "tables/film_want.jsonl":
            rows = [json.loads(l) for l in d.decode().split("\n") if l.strip()]
            rows[-1]["title"] = None  # NOT NULL
            d = "\n".join(json.dumps(x) for x in rows).encode()
        zout.writestr(n, d)
open(os.path.join(dt.imports_dir(tmp), "damaged.zip"), "wb").write(bad.getvalue())
C = Database(os.path.join(tmp, "c.sqlite3"))
res3 = dt.apply(C, tmp, "imports/damaged.zip")
with C.connect() as cc:
    check("damaged file: refused with an error and NOTHING imported (rolled back)", "error" in res3 and cc.execute("SELECT COUNT(*) FROM release").fetchone()[0] == 0
          and cc.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 0)

# a column the target does not have is left out with a warning; a settings-only import goes into a filled installation
extra = io.BytesIO()
with zipfile.ZipFile(os.path.join(tmp, r["name"])) as zin, zipfile.ZipFile(extra, "w") as zout:
    for n in zin.namelist():
        d = zin.read(n)
        if n == "manifest.json":
            m = json.loads(d); m["tables"]["muted_studio"]["columns"].append("future_column"); d = json.dumps(m).encode()
        zout.writestr(n, d)
open(os.path.join(dt.imports_dir(tmp), "extra.zip"), "wb").write(extra.getvalue())
D = Database(os.path.join(tmp, "d.sqlite3"))
chk = dt.check(D, tmp, "imports/extra.zip")
check("a column the target lacks is only a warning", chk.get("ok") and not chk["blockers"] and any("future_column" in w for w in chk["warnings"]))
chk = dt.check(B, tmp, r["name"], ["settings"])
check("settings only: allowed into a filled installation", chk.get("ok") and not chk["blockers"])

for bad_name in ("../x.zip", "exports/../../etc/passwd", "exports/a/b.zip", "other/x.zip", "exports/.hidden.zip", "exports/x.txt"):
    check(f"path trick refused: {bad_name}", "error" in dt.check(A, tmp, bad_name))
print("\nALL OK" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
