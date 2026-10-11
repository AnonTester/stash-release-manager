"""Taking over what Stash already knows (v0.186.0): the Stash API key and the stash-box endpoints with their keys (Stash > Settings >
Metadata Providers > Stash-box endpoints), so nobody has to copy them by hand.

The plugin's exec script reads them from Stash (server side, the keys never pass the browser) and posts them to POST /stash-boxes/sync;
apply_sync() below decides what goes where. Rules:

* The Stash API key always follows Stash (there is no field for it any more, Stash is the only place it is entered).
* StashDB and ThePornDB keys are filled when empty; a value somebody typed wins, except on an explicit "update from Stash" (`overwrite`).
* Every other stash-box is only OFFERED (status 'available'); a row is created only for the endpoints named in `add`, because every
  provider row is queried on each scan.
* Provider URLs are taken verbatim from Stash: stash ids in Stash are matched by the exact endpoint string.

Nothing here returns or logs a key.
"""
import re
from urllib.parse import urlsplit

import history
import metadata_provider_repo
from config import settings as env_settings
from routers.metadata_providers import assign_free_color
from routers.settings import get_setting_value

_KEY_MAX = 32


def normalize_endpoint(url):
    """Comparison form of an endpoint URL: trimmed, no trailing slash, lower case."""
    return (url or "").strip().rstrip("/").lower()


def classify(endpoint):
    """'stashdb' / 'theporndb' for the two built-in providers (by host), None for any other stash-box."""
    host = (urlsplit((endpoint or "").strip()).hostname or "").lower()
    if host == "stashdb.org" or host.endswith(".stashdb.org"):
        return "stashdb"
    if host == "theporndb.net" or host.endswith(".theporndb.net"):
        return "theporndb"
    return None


def slug_key(name, endpoint, taken):
    """A valid, unused `metadata_provider` key for a stash-box: the name as a slug (the host's first label when the name gives nothing),
    never one of the reserved keys, numbered when taken."""
    base = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    if not base:
        host = (urlsplit((endpoint or "").strip()).hostname or "").lower()
        base = re.sub(r"[^a-z0-9]+", "_", host.replace("www.", "").split(".")[0]).strip("_")
    if not base:
        base = "stashbox"
    if not base[0].isalpha():
        base = f"sb_{base}"
    base = base[:_KEY_MAX - 3]
    if len(base) < 2:
        base = f"{base}_sb"
    candidate, n = base, 1
    while candidate in taken or candidate in metadata_provider_repo.RESERVED_KEYS:
        n += 1
        candidate = f"{base}_{n}"
    return candidate


def _store(db, key, value):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value))
    history.event("info", "settings", f"Setting {key} taken over from Stash")


def _effective(db, key):
    return get_setting_value(db, key, getattr(env_settings, key, "")) or ""


def apply_sync(db, stash_api_key, boxes, add=(), overwrite=False, stash_url_suggested=""):
    """Applies what the plugin read from Stash and returns a report without keys:
    {stash_url: 'set'|'present'|'none', stash_api_key: 'updated'|'unchanged'|'none',
     boxes: [{name, endpoint, kind: 'stashdb'|'theporndb'|'other', provider_key, has_key, status}]}
    status: filled | present | updated | added | available | skipped (Stash has no key for it)."""
    report = {"stash_url": "present", "stash_api_key": "none", "boxes": []}

    # The Stash URL: only a suggestion (the address the browser uses), never over a value that is already there (database or .env).
    if not _effective(db, "stash_url"):
        suggested = (stash_url_suggested or "").strip().rstrip("/")
        if suggested:
            _store(db, "stash_url", suggested)
            report["stash_url"] = "set"
        else:
            report["stash_url"] = "none"

    # The Stash API key follows Stash. An empty answer (no login / no key generated) changes nothing.
    if stash_api_key:
        if _effective(db, "stash_api_key") != stash_api_key:
            _store(db, "stash_api_key", stash_api_key)
            report["stash_api_key"] = "updated"
        else:
            report["stash_api_key"] = "unchanged"

    wanted = {normalize_endpoint(e) for e in (add or ())}
    rows = metadata_provider_repo.list_all(db)
    by_url = {normalize_endpoint(r["url"]): r for r in rows}
    taken = {r["key"] for r in rows}
    builtin_done = set()

    for box in boxes or []:
        endpoint = (box.get("endpoint") or "").strip()
        key = box.get("api_key") or ""
        name = (box.get("name") or "").strip() or (urlsplit(endpoint).hostname or endpoint)
        if not endpoint:
            continue
        kind = classify(endpoint) or "other"
        entry = {"name": name, "endpoint": endpoint, "kind": kind, "provider_key": None, "has_key": bool(key), "status": "skipped"}
        report["boxes"].append(entry)

        if kind in ("stashdb", "theporndb"):
            entry["provider_key"] = kind
            if not key:
                continue  # skipped: Stash holds no key for this endpoint
            if kind in builtin_done:
                entry["status"] = "present"
                continue
            builtin_done.add(kind)
            key_setting = f"{kind}_api_key"
            current = _effective(db, key_setting)
            if current and current != key and not overwrite:
                entry["status"] = "present"
            elif current == key:
                entry["status"] = "present"
            else:
                _store(db, key_setting, key)
                entry["status"] = "updated" if current else "filled"
            # StashDB is queried at the configured endpoint, and stash ids are matched by it: follow Stash's exact string when none is set.
            # (ThePornDB's `theporndb_url` is its REST address, not the stash-box endpoint -- left alone.)
            if kind == "stashdb":
                url_current = get_setting_value(db, "stashdb_url", "")
                if not url_current or (overwrite and url_current != endpoint):
                    _store(db, "stashdb_url", endpoint)
            continue

        row = by_url.get(normalize_endpoint(endpoint))
        if row is not None:
            entry["provider_key"] = row["key"]
            if key and not row["api_key"]:
                metadata_provider_repo.update(db, row["key"], api_key=key)
                entry["status"] = "filled"
            elif key and overwrite and row["api_key"] != key:
                metadata_provider_repo.update(db, row["key"], api_key=key, url=endpoint)
                entry["status"] = "updated"
            else:
                entry["status"] = "present"
            continue

        if normalize_endpoint(endpoint) in wanted and key:
            new_key = slug_key(name, endpoint, taken)
            metadata_provider_repo.create(db, new_key, name, endpoint, key, True)
            assign_free_color(db, new_key)
            taken.add(new_key)
            by_url[normalize_endpoint(endpoint)] = {"key": new_key, "api_key": key}
            entry["provider_key"] = new_key
            entry["status"] = "added"
            history.event("info", "settings", f"Metadata provider {name} ({new_key}) added from Stash")
        else:
            entry["status"] = "available" if key else "skipped"
    return report
