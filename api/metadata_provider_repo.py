"""CRUD for ADDITIONAL stash-box GraphQL metadata providers (Metadata
Provider generalization, 2026-09-21) -- StashDB and ThePornDB are NOT
rows here, see db.py's own comment on the `metadata_provider` table for
why. A row's `key` doubles as the `provider` value stored in
watchlist/watermark/release, so it's validated here (not just at the
schema level, since the CHECK constraint on those tables was removed
precisely to allow this) rather than left to whatever a caller happens
to pass in.
"""
import re

RESERVED_KEYS = {"stashdb", "theporndb", "whisparr", "prowlarr", "sabnzbd", "stash"}
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


class InvalidKeyError(ValueError):
    pass


def validate_key(key):
    """Raises InvalidKeyError with a user-facing reason, or returns
    silently if `key` is fine. Called on create only -- a provider's key
    is its stable identity (used as `provider` everywhere else), so it's
    never editable after creation, same as this project's other natural-
    key rows (e.g. `setting.key`)."""
    if not _KEY_RE.match(key or ""):
        raise InvalidKeyError(
            "key must be 2-32 characters, lowercase letters/digits/underscore, starting with a letter"
        )
    if key in RESERVED_KEYS:
        raise InvalidKeyError(f"'{key}' is reserved for a built-in connection")


def list_all(db, enabled_only=False):
    query = "SELECT * FROM metadata_provider"
    if enabled_only:
        query += " WHERE enabled = 1"
    query += " ORDER BY name"
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query).fetchall()]


def get_one(db, key):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM metadata_provider WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None


def create(db, key, name, url, api_key="", enabled=True):
    validate_key(key)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO metadata_provider (key, name, url, api_key, enabled) VALUES (?, ?, ?, ?, ?)",
            (key, name, url, api_key, int(bool(enabled))),
        )
    return get_one(db, key)


def validate_color(color):
    """'' (automatic) or #rrggbb; raises InvalidKeyError otherwise (same error type the router already maps to a 400)."""
    if color and not _COLOR_RE.match(color):
        raise InvalidKeyError("color must be #rrggbb or empty")


def update(db, key, name=None, url=None, api_key=None, enabled=None, color=None):
    fields = {}
    if name is not None:
        fields["name"] = name
    if url is not None:
        fields["url"] = url
    if api_key is not None:
        fields["api_key"] = api_key
    if enabled is not None:
        fields["enabled"] = int(bool(enabled))
    if color is not None:
        validate_color(color)
        fields["color"] = color
    if not fields:
        return get_one(db, key)
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db.connect() as conn:
        conn.execute(f"UPDATE metadata_provider SET {cols} WHERE key = ?", (*fields.values(), key))
    return get_one(db, key)


def delete(db, key):
    with db.connect() as conn:
        conn.execute("DELETE FROM metadata_provider WHERE key = ?", (key,))
