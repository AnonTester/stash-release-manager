"""Film wishlist (v0.107.0): CRUD for `film_want`, see db.py. Films/groups the user wants -- separate from the scene releases."""
import json
import re
import unicodedata

_COLS = ("title", "year", "release_date", "studio_name", "duration_min", "performers", "image_url", "tpdb_id", "tpdb_json", "source",
         "source_ref", "match_class", "stash_group_id", "status", "note", "candidates_json", "seed_json")


def now():
    """SQLite-style UTC timestamp, like datetime('now')."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def norm_title(text):
    """'Alibi, The' -> 'thealibi'; case, accents, punctuation ignored."""
    t = (text or "").strip()
    m = re.match(r"^(.*),\s*(the|a|an)$", t, re.I)
    if m:
        t = f"{m.group(2)} {m.group(1)}"
    t = "".join(ch for ch in unicodedata.normalize("NFKD", t.lower()) if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", t)


def tpdb_url(slug):
    return f"https://theporndb.net/movies/{slug}" if slug else None


def _row(r):
    d = dict(r)
    for key in ("performers", "scene_ids"):
        try:
            d[key] = json.loads(d[key]) if d.get(key) else []
        except ValueError:
            d[key] = []
    try:
        d["stashdb"] = json.loads(d.pop("stashdb_json")) if d.get("stashdb_json") else None
    except ValueError:
        d["stashdb"] = None
    # The automatic movie search (v0.128.0): the candidate waiting for a yes/no and the names the user rejected.
    for key, empty in (("review_candidate", None), ("rejected_titles", [])):
        try:
            d[key] = json.loads(d[key]) if d.get(key) else empty
        except ValueError:
            d[key] = empty
    # The ThePornDB side of the row (v0.116.0): page link and the candidates of the last match WITHOUT their (big) records.
    try:
        d["seed"] = json.loads(d.pop("seed_json")) if d.get("seed_json") else None
    except ValueError:
        d["seed"] = None
    try:
        cands = json.loads(d.pop("candidates_json")) if d.get("candidates_json") else []
    except ValueError:
        cands = []
    d["candidates"] = [{**{k: v for k, v in c.items() if k != "record"}, "url": tpdb_url((c.get("record") or {}).get("slug"))} for c in cands]
    try:
        tpdb = json.loads(r["tpdb_json"]) if r["tpdb_json"] else {}
    except ValueError:
        tpdb = {}
    slug = tpdb.get("slug")
    # For the filter bar of the movie page ("incl. description" / "incl. tags", v0.138.0); the rest of the big record stays out of the list.
    d["description"] = (tpdb.get("description") or "")[:1500]
    d["tags"] = [t.get("name") for t in (tpdb.get("tags") or []) if isinstance(t, dict) and t.get("name")]
    d["tpdb_url"] = tpdb_url(slug)
    d.pop("tpdb_json", None)  # big; the import loads it by id
    return d


def get(db, film_id, with_json=False):
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM film_want WHERE id = ?", (int(film_id),)).fetchone()
    if not r:
        return None
    d = _row(r)
    if with_json:
        d["tpdb_json"] = json.loads(r["tpdb_json"]) if r["tpdb_json"] else None
    return d


def find_duplicate(db, tpdb_id=None, title=None, year=None):
    """The existing row for the same ThePornDB movie, else the same normalised title (+ year when both known)."""
    with db.connect() as conn:
        if tpdb_id:
            r = conn.execute("SELECT id FROM film_want WHERE tpdb_id = ?", (tpdb_id,)).fetchone()
            if r:
                return r["id"]
        key = norm_title(title)
        if key:
            for r in conn.execute("SELECT id, title, year FROM film_want WHERE status != 'dismissed'"):
                if norm_title(r["title"]) == key and (not year or not r["year"] or abs(int(year) - int(r["year"])) <= 1):
                    return r["id"]
    return None


def add(db, **fields):
    """Inserts a film; returns (id, created). An existing row for the same movie is returned instead (created False)."""
    dup = find_duplicate(db, fields.get("tpdb_id"), fields.get("title"), fields.get("year"))
    if dup:
        return dup, False
    values = {k: fields.get(k) for k in _COLS if fields.get(k) is not None}
    if isinstance(values.get("performers"), (list, tuple)):
        values["performers"] = json.dumps(values["performers"], ensure_ascii=False)
    for key in ("tpdb_json", "candidates_json", "seed_json"):
        if isinstance(values.get(key), (dict, list)):
            values[key] = json.dumps(values[key], ensure_ascii=False)
    cols = ", ".join(values)
    with db.connect() as conn:
        cur = conn.execute(f"INSERT INTO film_want ({cols}) VALUES ({', '.join('?' * len(values))})", tuple(values.values()))
        return cur.lastrowid, True


def update(db, film_id, **fields):
    allowed = set(_COLS) | {"download_client_id", "download_job_id", "grabbed_title", "grabbed_at", "last_search_at",
                            "last_search_result", "pending_import_path", "import_error", "imported_group_id", "mode", "scene_ids",
                            "stashdb_json", "review_candidate", "rejected_titles", "whisparr_id", "whisparr_state", "whisparr_checked_at",
                            "whisparr_file_state", "whisparr_file_checked_at", "whisparr_note", "library_choice", "as_scene", "parts_imported", "upgrade_wanted", "owned_height", "upgrading"}
    values = {k: v for k, v in fields.items() if k in allowed}
    if isinstance(values.get("performers"), (list, tuple)):  # (Link movie / Use candidate pass the ThePornDB performer names)
        values["performers"] = json.dumps(values["performers"], ensure_ascii=False)
    for key in ("scene_ids", "stashdb_json", "candidates_json", "seed_json", "review_candidate", "rejected_titles"):
        if isinstance(values.get(key), (dict, list)):
            values[key] = json.dumps(values[key], ensure_ascii=False)
    if not values:
        return
    if isinstance(values.get("tpdb_json"), (dict, list)):
        values["tpdb_json"] = json.dumps(values["tpdb_json"], ensure_ascii=False)
    sets = ", ".join(f"{k} = ?" for k in values) + ", updated_at = datetime('now')"
    with db.connect() as conn:
        conn.execute(f"UPDATE film_want SET {sets} WHERE id = ?", (*values.values(), int(film_id)))


def list_films(db, statuses=None, q=None, page=1, per_page=40, order="recent"):
    where, params = [], []
    if statuses:
        where.append(f"status IN ({','.join('?' * len(statuses))})")
        params += list(statuses)
    if q:
        where.append("(title LIKE ? OR studio_name LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql_order = {"recent": "updated_at DESC, id DESC", "title": "title COLLATE NOCASE", "year": "year DESC, title"}.get(order, "id DESC")
    with db.connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM film_want {clause}", params).fetchone()[0]
        limit = f"LIMIT {int(per_page)} OFFSET {(max(int(page), 1) - 1) * int(per_page)}" if per_page else ""
        rows = [_row(r) for r in conn.execute(f"SELECT * FROM film_want {clause} ORDER BY {sql_order} {limit}", params)]
    return {"total": total, "rows": rows}


def is_known_job(db, job_id):
    """True when a download job belongs to a film (the SABnzbd completion webhook only reacts to our own jobs)."""
    with db.connect() as conn:
        return conn.execute("SELECT 1 FROM film_want WHERE download_job_id = ? LIMIT 1", (job_id,)).fetchone() is not None


def candidate_record(db, film_id, tpdb_id):
    """The stored ThePornDB record of one candidate of the last match (for "Use this candidate")."""
    with db.connect() as conn:
        r = conn.execute("SELECT candidates_json FROM film_want WHERE id = ?", (int(film_id),)).fetchone()
    for c in (json.loads(r["candidates_json"]) if r and r["candidates_json"] else []):
        if c.get("id") == tpdb_id:
            return c.get("record")
    return None


def find_by_group(db, group_id):
    """The open (not dismissed) film row that belongs to an existing Stash group, else None."""
    with db.connect() as conn:
        # stash_group_id = a group the movie is for (the wishlist row came from it); imported_group_id = the group an import built (v0.127.1)
        r = conn.execute("SELECT id FROM film_want WHERE (stash_group_id = ? OR imported_group_id = ?) AND status != 'dismissed' ORDER BY id DESC LIMIT 1",
                         (str(group_id), str(group_id))).fetchone()
    return get(db, r["id"]) if r else None


def delete_untouched(db, ids):
    """Removes rows that were only just ADDED to the wishlist (status 'wanted'/'suggested', nothing grabbed) -- the Undo of "Add all missing".
    Returns how many were removed."""
    n = 0
    with db.connect() as conn:
        for fid in ids:
            n += conn.execute("DELETE FROM film_want WHERE id = ? AND status IN ('wanted', 'suggested') AND download_job_id IS NULL "
                              "AND mode = 'file'", (int(fid),)).rowcount
    return n


def counts(db):
    with db.connect() as conn:
        return {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM film_want GROUP BY status")}
