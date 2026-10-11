"""Where watchlist membership comes from (2026-09-26, "watchlist by tag").

Two modes, selected by the `watchlist_source` setting (see
routers/settings.py's DEFAULTS):

- 'favorites' (the original behavior since 2026-09-11): a Studio/
  Performer is watched when it's starred as Favorite in Stash.
- 'tag': it's watched when it carries the `watchlist_tag_name` tag
  (default "Watchlist") or any child tag of it. Favorite is then just a
  normal favorite again. Lets the watchlist be curated independently of
  what's favorited -- smaller watchlist, shorter scans and identify runs.

Everything that asks "which entities are watched" goes through here
(sync.py's watchlist sync, the ThePornDB identify job, the Search page's
"Watch" action, the Stash-page watchlist icon, the update hook) instead
of reading the Favorite flag directly, so the mode is decided in one
place.

Safety: in 'tag' mode a missing tag raises WatchlistSourceError rather
than returning an empty list -- sync.py would otherwise remove every
watchlist row (and its scan watermark) on the next run.
"""
from routers.settings import get_setting_value

MODE_FAVORITES = "favorites"
MODE_TAG = "tag"

TAG_DESCRIPTION = ("Studios/Performers with this tag (or a child tag) are on the Release Manager "
                   "watchlist: new scenes of theirs are tracked.")


class WatchlistSourceError(Exception):
    pass


def get_mode(db):
    return MODE_TAG if get_setting_value(db, "watchlist_source", MODE_FAVORITES) == MODE_TAG else MODE_FAVORITES


def get_tag_name(db):
    return (get_setting_value(db, "watchlist_tag_name", "Watchlist") or "Watchlist").strip()


def get_tag_id(db, stash_client, create=False):
    """The watchlist tag's Stash id, or None if it doesn't exist yet
    (created on demand when `create` is set)."""
    name = get_tag_name(db)
    if create:
        return stash_client.ensure_tag(name, TAG_DESCRIPTION)
    return stash_client.find_tag_id(name)


def _require_tag_id(db, stash_client):
    tag_id = get_tag_id(db, stash_client)
    if not tag_id:
        raise WatchlistSourceError(
            f"watchlist tag {get_tag_name(db)!r} not found in Stash -- not syncing, "
            "so the existing watchlist isn't wiped")
    return tag_id


def fetch_watched(db, stash_client, entity_type, extra_endpoints=None):
    """Every watched Studio/Performer, in stash_client's entity shape
    ({id, name, stashdb_id, theporndb_id, provider_ids})."""
    if get_mode(db) == MODE_TAG:
        tag_id = _require_tag_id(db, stash_client)
        fetch = stash_client.tagged_studios if entity_type == "studio" else stash_client.tagged_performers
        return fetch(tag_id, extra_endpoints=extra_endpoints)
    fetch = stash_client.favorited_studios if entity_type == "studio" else stash_client.favorited_performers
    return fetch(extra_endpoints=extra_endpoints)


def is_watched(db, stash_client, entity_type, local_id):
    """(watched, state) for one entity, read fresh from Stash. `state`
    is stash_client.entity_state()'s dict (None if the entity is gone)
    plus `direct_tag`: whether the watchlist tag itself (not only a
    child tag) is set -- the watchlist icon needs that to know whether
    un-watching can be done by removing the tag alone."""
    state = stash_client.entity_state(entity_type, local_id)
    if state is None:
        return False, None
    if get_mode(db) == MODE_FAVORITES:
        return bool(state["favorite"]), state
    tag_id = get_tag_id(db, stash_client)
    if not tag_id:
        return False, {**state, "direct_tag": False}
    watched_ids = stash_client.tag_with_descendants(tag_id)
    return bool(state["tag_ids"] & watched_ids), {**state, "direct_tag": str(tag_id) in state["tag_ids"]}


def set_watched(db, stash_client, entity_type, local_id, watched):
    """Tag mode: adds the watchlist tag, or removes it together with any
    child tags of it (otherwise a child tag would keep the entity
    watched and the icon would appear not to react). Favorites mode:
    sets/clears the Favorite flag (used by the inactive review list's
    "Remove from watchlist")."""
    if get_mode(db) != MODE_TAG:
        stash_client.set_favorite(entity_type, local_id, watched)
        return
    tag_id = get_tag_id(db, stash_client, create=watched)
    if not tag_id:
        return  # nothing to remove
    if watched:
        stash_client.update_entity_tags(entity_type, [local_id], [tag_id], "ADD")
    else:
        stash_client.update_entity_tags(entity_type, [local_id], sorted(stash_client.tag_with_descendants(tag_id)),
                                        "REMOVE")


def watch_tag_id_for_new_entity(db, stash_client):
    """For the Search page's "Watch" action: the tag to put on a newly
    created/existing entity in tag mode, None in favorites mode (then it
    gets the Favorite flag instead)."""
    return get_tag_id(db, stash_client, create=True) if get_mode(db) == MODE_TAG else None


def switch_guard(db, stash_client, key, value):
    """routers/settings.py's `_watchlist_guard`: refuses a switch to tag
    mode (or a rename while in tag mode) when that tag doesn't exist or
    has no Studios/Performers on it -- the next sync would drop the whole
    watchlist. The migration (POST /watchlist/migrate-to-tag) tags the
    current favorites first and then switches itself."""
    if key == "watchlist_source":
        if value != MODE_TAG or get_mode(db) == MODE_TAG:
            return None
        name = get_tag_name(db)
    else:  # watchlist_tag_name
        if get_mode(db) != MODE_TAG or value == get_tag_name(db):
            return None
        name = value
    try:
        tag_id = stash_client.find_tag_id(name)
        count = 0
        if tag_id:
            criterion = {"tags": stash_client.tag_criterion(tag_id)}
            count = stash_client.count_entities("studio", criterion) + stash_client.count_entities("performer", criterion)
    except Exception as e:
        return f"couldn't check the watchlist tag in Stash: {e}"
    if count == 0:
        return (f"tag {name!r} has no Studios/Performers in Stash -- switching now would empty the watchlist. "
                "Use the migration (tags all current favorites first) or tag some entities first.")
    return None
