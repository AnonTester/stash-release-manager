"""Sanity check for everything that moves or renames files (v0.197.0): Import folder, Organize library, the "Organized" hook.

The danger it guards against: ANOTHER plugin that renames files when a scene is updated (renamerOnUpdate and its relatives, a hook on
Scene.Update.Post / Scene.Create.Post). Stash updates the scene after every move (the scan), that update triggers the other renamer, which moves
the file to ITS template -- and our next Organize run moves it back: an endless ping-pong, broken Whisparr/SRM paths, "Source file missing".

Checks (all read-only):
- other renamers: an installed plugin with a hook on scene events and rename/move wording in its id, name, description or hook text;
  `enabled` comes from Stash. Enabled = error/warning, installed but disabled = a note.
- the target folders of the formats are inside a Stash library path (else Stash never sees the moved files);
- the formats cannot give every scene the same name;
- Whisparr scope "Full Whisparr" (Whisparr names the files itself) while the Organized hook is on;
- files that were moved by us and then found somewhere else again (History: "Moved again by something else", last 7 days).
"""
import logging
import re

logger = logging.getLogger(__name__)

OWN_ID = "stash-release-manager"
SCENE_EVENTS = ("Scene.Update.Post", "Scene.Create.Post")
RENAMER_RE = re.compile(r"renam|move[^.]{0,12}files?|file ?mover|organi[sz]e[^.]{0,12}(?:files?|librar)", re.I)
_QUERY = "{ plugins { id name enabled description hooks { name description hooks } } }"
_QUERY_OLD = "{ plugins { id name description hooks { name description hooks } } }"  # a Stash without the `enabled` field


def renamer_plugins(stash):
    """[{id, name, enabled}] of the installed plugins that look like file renamers (ours excluded); raises when Stash cannot be asked."""
    try:
        plugins = stash._graphql(_QUERY, {})["plugins"] or []
    except Exception:
        plugins = stash._graphql(_QUERY_OLD, {})["plugins"] or []
    found = []
    for p in plugins:
        if p["id"] == OWN_ID:
            continue
        hooks = p.get("hooks") or []
        if not any(ev in (h.get("hooks") or []) for h in hooks for ev in SCENE_EVENTS):
            continue  # a renamer that reacts to scene updates hooks scene events
        text = " ".join([p["id"], p.get("name") or "", p.get("description") or "",
                         *[f"{h.get('name') or ''} {h.get('description') or ''}" for h in hooks]])
        if RENAMER_RE.search(text):
            found.append({"id": p["id"], "name": p.get("name") or p["id"], "enabled": p.get("enabled", True)})
    return found


def active_renamers(stash):
    """The ENABLED other renamers; [] when Stash cannot be asked (the check never blocks on its own failure)."""
    try:
        return [r for r in renamer_plugins(stash) if r["enabled"]]
    except Exception:
        logger.exception("renamer check: couldn't read Stash's plugin list")
        return []


def conflict_text(active):
    names = ", ".join(r["name"] for r in active)
    return (f"Another plugin that renames or moves files is enabled in Stash ({names}). After every move Stash updates the scene, that triggers "
            "the other renamer, and the files would be moved back and forth. Disable it in Stash (Settings > Plugins) or use only one of the two.")


def _first_folder(fmt):
    first = (fmt or "").split("/")[0].strip()
    return first if first and "{" not in first and "<" not in first else ""


def sanity(stash, setting, db=None, show_renamers=False):
    """{ok, hook, renamers, issues: [{level: error|warning|info|ok, text}]} -- see the module docstring.
    Other renamers are only REPORTED while the Organized hook is on (two renamers at work at once is the conflict) -- or when `show_renamers`
    is set, which the plugin does after a manual move was held back by the guard (v0.202.2)."""
    issues = []
    hook = (setting("organized_hook") or "off").strip()

    # 1. other renamers
    try:
        renamers = renamer_plugins(stash)
    except Exception as e:
        renamers = []
        issues.append({"level": "warning", "text": f"Stash's plugin list could not be read ({e}): other renaming plugins were not looked for."})
    else:
        active = [r for r in renamers if r["enabled"]]
        if hook == "on" or show_renamers:
            if active:
                issues.append({"level": "error" if hook == "on" else "warning", "text": conflict_text(active)
                               + (" The Organized hook is on: it is held back (preview only) as long as this is the case." if hook == "on" else "")})
            else:
                issues.append({"level": "ok", "text": "No other enabled plugin that renames or moves files was found in Stash."})

    # 2. target folders inside a Stash library path
    root = (setting("whisparr_root_folder_path") or "/data").rstrip("/")
    try:
        libraries = [s["path"].rstrip("/") for s in stash._graphql("{ configuration { general { stashes { path } } } }", {})["configuration"]["general"]["stashes"]]
    except Exception:
        libraries = None
    targets = {"scenes": _first_folder(setting("scene_folder_format")), "movies": _first_folder(setting("movie_folder_format"))}
    for label, folder in targets.items():
        if not folder:
            continue
        path = f"{root}/{folder}"
        if libraries is None:
            continue
        if not any(path == lib or path.startswith(lib + "/") or lib.startswith(path + "/") for lib in libraries):
            issues.append({"level": "warning", "text": f"The {label} folder of the format ({path}) is not inside any Stash library path "
                                                       f"({', '.join(libraries) or 'none'}): Stash would not see moved files there. Add the folder to Stash's libraries "
                                                       "(note upper/lower case) or change the folder format."})
        else:
            issues.append({"level": "ok", "text": f"The {label} folder ({path}) is inside a Stash library."})

    # 3. formats that give every scene the same name
    if not re.search(r"\{(Scene Title|Release Date)\}", setting("scene_naming_format") or ""):
        issues.append({"level": "error", "text": "The scene file format has neither {Scene Title} nor {Release Date}: different scenes would get the same name."})
    if "{Movie Title}" not in (setting("movie_naming_format") or ""):
        issues.append({"level": "error", "text": "The movie file format has no {Movie Title}: different films would get the same name."})

    # 4. Whisparr names the files itself
    if db is not None and hook in ("preview", "on"):
        try:
            from routers.settings import resolve_whisparr_scope_preset
            if resolve_whisparr_scope_preset(db) == "full_whisparr":
                issues.append({"level": "warning", "text": "The Whisparr scope is \"Full Whisparr management\": Whisparr imports and names the files itself, "
                                                           "so moving them here as well would fight with it."})
        except Exception:
            logger.exception("renamer check: couldn't read the Whisparr scope")

    # 5. files moved by us and moved again by something else
    if db is not None:
        try:
            with db.connect() as conn:
                row = conn.execute("""SELECT COUNT(*) AS n FROM history WHERE category IN ('import', 'organize') AND message LIKE 'Moved again by something else%'
                                      AND at >= strftime('%Y-%m-%d %H:%M:%S', 'now', '-7 days')""").fetchone()
            if row["n"]:
                issues.append({"level": "error", "text": f"{row['n']} file(s) moved by this plugin were moved again by something else within minutes "
                                                          "(see History, 'Moved again by something else'): another tool is renaming them. Find it before moving more."})
        except Exception:
            logger.exception("renamer check: couldn't read the History")
    return {"ok": not any(i["level"] == "error" for i in issues), "hook": hook, "renamers": renamers, "issues": issues}
