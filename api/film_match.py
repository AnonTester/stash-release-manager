"""Seed match for the film wishlist (v0.107.0): films we know by NAME only (the 72 empty groups deleted on 2026-10-04, the empty groups
still in Stash) are looked up on ThePornDB (ONE /movies request per film, with a pause) and stored as 'suggested' film_want rows
with the match class -- nothing is wanted until the user accepts it. Same classes as the earlier library dry run:
  sure   = same title + year +-1 + length within 10 %
  likely = same title, year ok (or unknown), length differs/unknown
  unsure = same title but another year, or the title only contained
  none   = no usable result (the film is still listed so it can be searched by hand or dismissed)"""
import json
import logging
import os
import re
import threading
import time

import film_repo

logger = logging.getLogger(__name__)

DELETED_GROUPS_FILE = "movie_groups_deleted_20261004.json"
_JUNK = re.compile(r"\s*[-–]\s*Pornofilme.*$|\s*\((?:[^)]*(?:Video|DVD|Films?|Productions?)[^)]*)\)\s*$|\s+DVD$", re.I)


def clean_title(name):
    t = (name or "").strip()
    for _ in range(2):
        t = _JUNK.sub("", t).strip()
    return t


def classify(title, year, minutes, results):
    cands = []
    for d in results:
        dy = int((d.get("date") or "0000")[:4] or 0)
        dm = (d.get("duration") or 0) / 60
        same = film_repo.norm_title(d.get("title")) == film_repo.norm_title(title)
        nt, nd = film_repo.norm_title(title), film_repo.norm_title(d.get("title"))
        contained = (not same) and len(nt) >= 6 and (nt in nd or nd in nt)
        cands.append({"record": d, "id": d["id"], "title": d.get("title"), "year": dy, "minutes": round(dm, 1), "same": same,
                      "contained": contained,
                      "year_ok": (not year) or (bool(dy) and abs(dy - year) <= 1),
                      "year_known": bool(year) and bool(dy) and abs(dy - year) <= 1,
                      "length_ok": bool(minutes) and bool(dm) and abs(dm / minutes - 1) <= 0.10})
    sure = [c for c in cands if c["same"] and c["year_known"] and c["length_ok"]]
    if sure:
        return "sure", min(sure, key=lambda c: abs(c["minutes"] - (minutes or 0))), cands
    likely = [c for c in cands if c["same"] and c["year_ok"]]
    if likely:
        return "likely", likely[0], cands
    unsure = [c for c in cands if c["same"] or c["contained"]]
    if unsure:
        return "unsure", unsure[0], cands
    return "none", None, cands


def candidates_json(cands, limit=5):
    """The best candidates of a match (title-equal / contained first, then year and length fit) WITH their ThePornDB records, so a
    candidate can be taken later with one click ("Use this candidate") -- no second search."""
    ranked = sorted(cands, key=lambda c: (c["same"], c["contained"], c["year_ok"], c["length_ok"]), reverse=True)[:limit]
    out = []
    for c in ranked:
        rec = c["record"]
        out.append({"id": c["id"], "title": c["title"], "year": c["year"], "minutes": c["minutes"], "site": (rec.get("site") or {}).get("name"),
                    "performers": len(rec.get("performers") or []), "same": c["same"], "year_ok": c["year_ok"], "length_ok": c["length_ok"],
                    "record": rec})
    return out


def match_fields(seed, cls, best, cands):
    """film_want fields for the result of one match: the ThePornDB movie is taken over for sure/likely, otherwise the seed's own data
    stays; the candidates and the seed are always kept."""
    rec = best["record"] if best and cls in ("sure", "likely") else None
    fields = {
        "title": rec.get("title") if rec else seed["title"],
        "year": (best["year"] or seed["year"]) if rec else seed["year"],
        "release_date": rec.get("date") if rec else seed["date"],
        "studio_name": (rec.get("site") or {}).get("name") if rec else seed.get("studio"),
        "duration_min": (best["minutes"] or seed["minutes"]) if rec else seed["minutes"],
        "image_url": rec.get("poster") if rec else None,
        "tpdb_id": rec["id"] if rec else None, "tpdb_json": rec, "match_class": cls,
        "candidates_json": candidates_json(cands),
        "seed_json": {"title": seed["title"], "year": seed["year"], "minutes": seed["minutes"], "studio": seed.get("studio"), "date": seed["date"],
                      "urls": seed.get("urls") or []},
        "note": f"Unsure match, check it: {best['title']} ({best['year']})" if cls == "unsure" and best else None,
    }
    return fields


def seeds_from_deleted_groups(data_dir):
    path = os.path.join(data_dir, DELETED_GROUPS_FILE)
    with open(path, encoding="utf-8") as fh:
        groups = json.load(fh)
    out = []
    for g in groups:
        date = g.get("date") or ""
        duration = g.get("duration")
        out.append({"ref": f"deleted-group:{g['id']}", "title": clean_title(g["name"]), "year": int(date[:4]) if date[:4].isdigit() else None,
                    "minutes": round(duration / 60, 1) if isinstance(duration, (int, float)) and duration else None,
                    "studio": (g.get("studio") or {}).get("name") if isinstance(g.get("studio"), dict) else None, "date": date or None,
                    "source": "deleted-group", "urls": [u for u in (g.get("urls") or []) if isinstance(u, str)]})
    return out


def seeds_from_empty_groups(movies):
    """Groups in Stash without any file (Maintenance > Movies & groups > groups without files)."""
    out = []
    for g in movies.groups_without_files():
        date = g.get("date") or ""
        out.append({"ref": f"empty-group:{g['id']}", "title": clean_title(g["name"]), "year": int(date[:4]) if date[:4].isdigit() else None,
                    "minutes": None, "studio": None, "date": date or None, "source": "empty-group", "group_id": g["id"]})
    return out


class SeedJob:
    """One background run at a time; state is read by the UI poll."""

    def __init__(self, db, get_client, pause=0.8):
        self.db, self.get_client, self.pause = db, get_client, pause
        self.state = {"running": False, "done": 0, "total": 0, "requests": 0, "added": {}, "error": None, "source": None}
        self._lock = threading.Lock()

    def start(self, seeds, source):
        with self._lock:
            if self.state["running"]:
                return {"error": "a seed match is already running"}
            self.state = {"running": True, "done": 0, "total": len(seeds), "requests": 0, "added": {}, "error": None, "source": source}
        threading.Thread(target=self._run, args=(seeds,), daemon=True).start()
        return {"ok": True, "total": len(seeds)}

    def start_rematch(self, films, data_dir):
        with self._lock:
            if self.state["running"]:
                return {"error": "a match is already running"}
            self.state = {"running": True, "done": 0, "total": len(films), "requests": 0, "added": {}, "error": None, "source": "rematch"}
        threading.Thread(target=self._run_rematch, args=(films, data_dir), daemon=True).start()
        return {"ok": True, "total": len(films)}

    def _run_rematch(self, films, data_dir):
        try:
            client = self.get_client()
            if client is None:
                self.state["error"] = "ThePornDB is not configured"
                return
            for film in films:
                seed = _seed_of(film, data_dir)
                try:
                    resp = client._get("/movies", params={"q": seed["title"], "per_page": 8})
                    resp.raise_for_status()
                    results = resp.json().get("data") or []
                    self.state["requests"] += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning("movie rematch: request failed for %r: %s", seed["title"], e)
                    self.state["error"] = f"{type(e).__name__}: {e}"
                    time.sleep(10)
                    self.state["done"] += 1
                    continue
                cls, best, cands = classify(seed["title"], seed["year"], seed["minutes"], results)
                fields = match_fields(seed, cls, best, cands)
                try:
                    film_repo.update(self.db, film["id"], **fields)
                except Exception as e:  # noqa: BLE001  (e.g. the ThePornDB movie is already on another row)
                    logger.warning("movie rematch: could not store the match for %r: %s", seed["title"], e)
                    fields.update(tpdb_id=None, tpdb_json=None, match_class="unsure" if cls in ("sure", "likely") else cls,
                                  note="This ThePornDB movie is already on another row -- check for a duplicate")
                    film_repo.update(self.db, film["id"], **fields)
                self.state["added"][cls] = self.state["added"].get(cls, 0) + 1
                self.state["done"] += 1
                time.sleep(self.pause)
        except Exception as e:  # noqa: BLE001
            logger.exception("movie rematch failed")
            self.state["error"] = f"{type(e).__name__}: {e}"
        finally:
            self.state["running"] = False

    def _run(self, seeds):
        try:
            client = self.get_client()
            if client is None:
                self.state["error"] = "ThePornDB is not configured"
                return
            with self.db.connect() as conn:
                known = {r[0] for r in conn.execute("SELECT source_ref FROM film_want WHERE source_ref IS NOT NULL")}
            for seed in seeds:
                if seed["ref"] in known or not seed["title"]:
                    self.state["done"] += 1
                    continue
                try:
                    resp = client._get("/movies", params={"q": seed["title"], "per_page": 8})
                    resp.raise_for_status()
                    results = resp.json().get("data") or []
                    self.state["requests"] += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning("movie seed match: request failed for %r: %s", seed["title"], e)
                    self.state["error"] = f"{type(e).__name__}: {e}"
                    time.sleep(10)
                    self.state["done"] += 1
                    continue
                cls, best, cands = classify(seed["title"], seed["year"], seed["minutes"], results)
                film_repo.add(self.db, **match_fields(seed, cls, best, cands), source=seed["source"], source_ref=seed["ref"],
                              status="suggested", stash_group_id=seed.get("group_id"))
                self.state["added"][cls] = self.state["added"].get(cls, 0) + 1
                self.state["done"] += 1
                time.sleep(self.pause)
        except Exception as e:  # noqa: BLE001
            logger.exception("movie seed match failed")
            self.state["error"] = f"{type(e).__name__}: {e}"
        finally:
            self.state["running"] = False


def _seed_of(film, data_dir):
    """What we knew about the film before any match: the stored seed, else the original deleted group, else its current fields."""
    if film.get("seed"):
        return film["seed"]
    ref = film.get("source_ref") or ""
    if ref.startswith("deleted-group:"):
        for seed in seeds_from_deleted_groups(data_dir):
            if seed["ref"] == ref:
                return {"title": seed["title"], "year": seed["year"], "minutes": seed["minutes"], "studio": seed.get("studio"), "date": seed["date"],
                        "urls": seed.get("urls") or []}
    return {"title": film["title"], "year": film.get("year"), "minutes": film.get("duration_min"), "studio": film.get("studio_name"),
            "date": film.get("release_date")}


def start_rematch(job, films, data_dir):
    """Matches the given suggested films again (ONE ThePornDB request each) -- keeps the candidates this time. Skips what the user linked."""
    rows = [f for f in films if f["status"] == "suggested" and f.get("match_class") in ("likely", "unsure", "none")]
    if not rows:
        return {"error": "no unclear suggestion to match again"}
    return job.start_rematch(rows, data_dir)
