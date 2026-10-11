"""Maintenance lists for movies / groups (2026-09-29, user decisions in the roadmap memory).

Stash model: a film is a Group; a single-file film = one scene + a Group of the same title;
a film in parts (CD1/CD2, real scenes) = one Group with one scene per part (scene_index).
Only the movie folders (/data/Movies, /data/movies) are looked at -- normal scenes have no group
by design and never show up here.

  videos_without_group()   film folders whose videos belong to no Group, with a suggested title
                           from the folder name "Title (Year)"; nothing is created without a click
  performers_without_scenes()  performers with 0 scenes (the Watchlist tag marks intended ones)
  groups_without_files()   groups with no scene at all, or only scenes without a file
"""
import film_names
import base64
import logging
import os
import re
from collections import defaultdict

logger = logging.getLogger(__name__)

MOVIE_ROOTS = ("/data/Movies/", "/data/movies/")
SHORT_MINUTES = 20
_FOLDER_RE = re.compile(r"^(?P<title>.*?)\s*\((?P<year>\d{4})?\)\s*$")
_PART_RE = re.compile(r"(?i)(cd|disc|disk|part|pt)[\s._\-]?(\d+|[a-d])\b")

_NO_GROUP_QUERY = """
query { findScenes(filter: {per_page: -1}, scene_filter: {groups: {modifier: IS_NULL}}) {
  scenes { id title date files { id path duration width height } } } }
"""
_PERFORMERS_QUERY = """
query { findPerformers(filter: {per_page: -1}, performer_filter: {scene_count: {value: 0, modifier: EQUALS}}) {
  performers { id name disambiguation favorite tags { id name } } } }
"""
_ALL_GROUPS_QUERY = """
query { findGroups(filter: {per_page: -1}) { groups { id name date scene_count } } }
"""
_GROUPS_QUERY = """
query { findGroups(filter: {per_page: -1}) { groups { id name date scene_count scenes { id files { id } } } } }
"""


class Movies:
    def __init__(self, stash_client, setting_getter):
        self.stash = stash_client
        self.setting = setting_getter

    def movie_roots(self):
        """The film folders: /Movies/ and /movies/ plus the first folder of the movie folder template (v0.194.0), e.g. /data/Films/."""
        return film_names.movie_roots(self.setting("whisparr_root_folder_path"), self.setting("movie_folder_format"))

    # ---------- videos without a group (movie folders only) ----------

    @staticmethod
    def _film_key(path, root):
        """(folder path, is_film_folder): a folder named 'Title (Year)' is one film; a genre folder
        (e.g. /data/movies/Anal/) is not -- each video in it is its own candidate."""
        rel_dir = os.path.dirname(path)[len(root):].strip("/")
        leaf = rel_dir.split("/")[-1] if rel_dir else ""
        if leaf and _FOLDER_RE.match(leaf):
            return os.path.dirname(path), True
        return path, False

    def videos_without_group(self):
        scenes = self.stash._graphql(_NO_GROUP_QUERY, {})["findScenes"]["scenes"]
        by_title = defaultdict(list)  # normalized title -> existing groups (an empty one is a ready-made target)
        for g in self.stash._graphql(_ALL_GROUPS_QUERY, {})["findGroups"]["groups"]:
            by_title[self._norm(g["name"])].append(g)
        folders = defaultdict(list)
        meta = {}
        roots = self.movie_roots()
        for s in scenes:
            for f in s["files"]:
                root = next((r for r in roots if f["path"].startswith(r)), None)
                if not root:
                    continue
                key, is_film_dir = self._film_key(f["path"], root)
                folders[key].append({"scene_id": s["id"], "title": s["title"], "file": os.path.basename(f["path"]),
                                     "minutes": round((f["duration"] or 0) / 60, 1)})
                meta[key] = (root, is_film_dir)
        rows = []
        for key, vids in folders.items():
            root, is_film_dir = meta[key]
            leaf = os.path.basename(key) if is_film_dir else os.path.splitext(os.path.basename(key))[0]
            m = _FOLDER_RE.match(leaf)
            title = (m.group("title") if m else leaf).strip()
            year = (m.group("year") if m else None) or None
            vids.sort(key=lambda v: v["file"].lower())
            total = round(sum(v["minutes"] for v in vids), 1)
            flags = []
            if total < SHORT_MINUTES:
                flags.append("under 20 min -- probably not a film")
            if len(vids) > 1:
                flags.append("cd/part split" if sum(1 for v in vids if _PART_RE.search(v["file"])) >= 2
                             else f"{len(vids)} videos (scenes of one film?)")
            cands = by_title.get(self._norm(title), [])
            if year:  # prefer a group of the same year when there are several
                cands = sorted(cands, key=lambda g: (g.get("date") or "")[:4] != year)
            match = ({"id": cands[0]["id"], "name": cands[0]["name"], "date": cands[0].get("date"),
                      "scenes": cands[0]["scene_count"]} if cands else None)
            rows.append({"folder": key[len(root):] if key.startswith(root) else key, "root": root.rstrip("/"),
                         "title": title, "year": year, "minutes": total, "videos": vids, "flags": flags,
                         "existing_group": match})
        rows.sort(key=lambda r: (r["root"], r["folder"].lower()))
        return rows

    @staticmethod
    def _norm(text):
        return re.sub(r"[^a-z0-9]", "", (text or "").lower())

    def create_group(self, title, scene_ids, group_id=None):
        """Puts the scenes in a Group in the given order (scene_index counting on from the group's
        current scenes). Without `group_id` a new Group named `title` is created; with one the scenes
        join that existing group (e.g. an empty one made from the provider's metadata). The scenes'
        other groups are kept. Refuses an empty title (new group) / no scenes."""
        title = (title or "").strip()
        # One scene can hold several files of the same film folder: every scene goes in once, in the given order.
        scene_ids = list(dict.fromkeys(str(i) for i in scene_ids or []))
        if not scene_ids or (not group_id and not title):
            raise ValueError("a title and at least one scene are required")
        start = 1
        if group_id:
            g = self.stash._graphql("query($id: ID!) { findGroup(id: $id) { id name scene_count } }",
                                    {"id": str(group_id)})["findGroup"]
            if not g:
                raise ValueError("group not found")
            title, start = g["name"], (g["scene_count"] or 0) + 1
        else:
            group_id = self.stash._graphql("mutation($i: GroupCreateInput!) { groupCreate(input: $i) { id } }",
                                           {"i": {"name": title}})["groupCreate"]["id"]
        for index, sid in enumerate(scene_ids, start):
            cur = self.stash._graphql("query($id: ID!) { findScene(id: $id) { groups { group { id } scene_index } } }",
                                      {"id": sid})["findScene"]["groups"]
            groups = [{"group_id": g["group"]["id"], "scene_index": g["scene_index"]} for g in cur]
            groups.append({"group_id": group_id, "scene_index": index})
            self.stash._graphql("mutation($i: SceneUpdateInput!) { sceneUpdate(input: $i) { id } }",
                                {"i": {"id": sid, "groups": groups}})
        return {"group_id": group_id, "title": title, "scenes": len(scene_ids)}

    # ---------- Groups from ThePornDB movies (v0.94.0) ----------

    def local_studio_index(self):
        """{normalised name or alias: [local studio ids]} -- a ThePornDB movie's studio is only linked when a local studio of that name
        (or alias) EXISTS and is unambiguous; no studio is created."""
        rows = self.stash._graphql("{ findStudios(filter: {per_page: -1}) { studios { id name aliases } } }", {})["findStudios"]["studios"]
        index = defaultdict(list)
        for st in rows:
            for name in [st["name"]] + list(st.get("aliases") or []):
                key = self._norm(name)
                if key and st["id"] not in index[key]:
                    index[key].append(st["id"])
        return index

    @staticmethod
    def _data_url(url):
        """Image as a data: URL (what Stash's groupCreate wants for front_image/back_image); None when it can't be fetched."""
        import requests
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200 or not r.content:
                return None
            kind = r.headers.get("content-type", "image/jpeg").split(";")[0]
            return f"data:{kind};base64,{base64.b64encode(r.content).decode()}"
        except Exception:
            logger.exception("movies: image download failed: %s", url)
            return None

    def tpdb_group_input(self, movie, studio_index):
        """GroupCreateInput fields from a ThePornDB /movies/{id} record -- GROUP data only (name, date, length, director, synopsis, urls,
        studio when a local one exists, front/back cover). Returns (input, notes)."""
        notes = []
        directors = []
        for d in movie.get("directors") or []:
            name = d.get("name") if isinstance(d, dict) else d
            if name:
                directors.append(str(name))
        urls = [u for u in (movie.get("url"), f"https://theporndb.net/movies/{movie['slug']}" if movie.get("slug") else None) if u]
        name = movie["title"].strip()
        art = re.match(r"^(.*?),\s*(the|a|an)$", name, re.I)  # ThePornDB lists "Driver, the": the Group is called "The Driver"
        if art:
            name = f"{art.group(2).capitalize()} {art.group(1)}"
        inp = {"name": name, "urls": urls}
        if movie.get("date"):
            inp["date"] = movie["date"]
        if movie.get("duration"):
            inp["duration"] = int(movie["duration"])
        if directors:
            inp["director"] = ", ".join(directors)
        if movie.get("description"):
            inp["synopsis"] = movie["description"]
        site = (movie.get("site") or {}).get("name")
        ids = studio_index.get(self._norm(site), []) if site else []
        if len(ids) == 1:
            inp["studio_id"] = ids[0]
        else:
            notes.append(f"studio {site!r}: " + ("no local studio of that name" if not ids else f"ambiguous ({len(ids)} local studios)"))
        poster = movie.get("poster")
        front = self._data_url(poster) if isinstance(poster, str) and poster.startswith("http") else None
        if front:
            inp["front_image"] = front
        else:
            notes.append("no front cover")
        back_url = movie.get("back_image")
        if isinstance(back_url, str) and back_url.startswith("http"):
            back = self._data_url(back_url)
            if back:
                inp["back_image"] = back
        return inp, notes

    def create_from_tpdb(self, movie, scene_ids, studio_index):
        """Creates the Group from the ThePornDB movie and puts the scene(s) in it (scene data stays untouched). Returns
        {"group_id", "name", "notes", "studio"}."""
        inp, notes = self.tpdb_group_input(movie, studio_index)
        group_id = self.stash._graphql("mutation($i: GroupCreateInput!) { groupCreate(input: $i) { id } }", {"i": inp})["groupCreate"]["id"]
        self.create_group(inp["name"], scene_ids, group_id=group_id)
        return {"group_id": group_id, "name": inp["name"], "notes": notes, "studio": bool(inp.get("studio_id"))}

    # ---------- performers without scenes ----------

    def performers_without_scenes(self):
        wl_name = (self.setting("watchlist_tag_name") or "Watchlist") if self.setting else "Watchlist"
        rows = []
        for p in self.stash._graphql(_PERFORMERS_QUERY, {})["findPerformers"]["performers"]:
            tags = [t["name"] for t in p["tags"]]
            rows.append({"id": p["id"], "name": p["name"], "disambiguation": p.get("disambiguation") or "",
                         "favorite": p["favorite"], "on_watchlist": wl_name in tags or p["favorite"]})
        rows.sort(key=lambda r: r["name"].lower())
        return rows

    # ---------- groups without files ----------

    def groups_without_files(self):
        rows = []
        for g in self.stash._graphql(_GROUPS_QUERY, {})["findGroups"]["groups"]:
            with_files = [s for s in g["scenes"] if s["files"]]
            if with_files:
                continue
            rows.append({"id": g["id"], "name": g["name"], "date": g.get("date"),
                         "scenes": len(g["scenes"]),
                         "reason": "no scene at all" if not g["scenes"] else "its scenes have no file"})
        rows.sort(key=lambda r: r["name"].lower())
        return rows
