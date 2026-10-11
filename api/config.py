"""Environment-based configuration for the sidecar. Values come from
the container/process environment (see docker-compose.yml / .env),
not Stash plugin settings -- the exec bridge only forwards the
sidecar URL; since v0.186.0 it also hands over what it reads from Stash
itself (API key, stash-box keys, see stashbox_sync.py). Everything else
the sidecar needs to reach Stash/StashDB/Whisparr lives here so the
sidecar can also run standalone.
"""
import os


class Settings:
    def __init__(self):
        self.data_dir = os.environ.get("DATA_DIR", "./data")
        # Development / standby copy (v0.178.0): started with SRM_DEV_MODE=1 an instance runs NO background job (no scan, search, import, sync,
        # clean-up) and no Stash event connection. A deployment property on purpose (environment, not a setting): settings travel with an
        # export / import, this must not. Manual actions in the UI still work.
        self.dev_mode = os.environ.get("SRM_DEV_MODE", "").strip().lower() in ("1", "true", "yes", "on")
        self.db_path = os.path.join(self.data_dir, "release_watcher.sqlite3")

        self.stash_url = os.environ.get("STASH_URL", "").rstrip("/")
        self.stash_api_key = os.environ.get("STASH_API_KEY", "")

        self.stashdb_url = os.environ.get("STASHDB_URL", "https://stashdb.org/graphql")
        self.stashdb_api_key = os.environ.get("STASHDB_API_KEY", "")

        self.whisparr_url = os.environ.get("WHISPARR_URL", "").rstrip("/")
        self.whisparr_api_key = os.environ.get("WHISPARR_API_KEY", "")

        # ThePornDB is a plain REST API (Bearer-token auth), not a
        # stash-box GraphQL endpoint like StashDB -- see
        # theporndb_client.py's module docstring.
        self.theporndb_url = os.environ.get("THEPORNDB_URL", "https://api.theporndb.net")
        self.theporndb_api_key = os.environ.get("THEPORNDB_API_KEY", "")
        self.whisparr_quality_profile_id = int(os.environ.get("WHISPARR_QUALITY_PROFILE_ID", "1"))
        self.whisparr_root_folder_path = os.environ.get("WHISPARR_ROOT_FOLDER_PATH", "/data/")

        # This sidecar's own local mount of the same physical share
        # Whisparr/Stash call `/data` on their end (Milestone 2,
        # 2026-09-17, see docker-compose.yml's own comment on why this
        # sidecar needs a second, differently-named mount of it).
        self.local_media_mount = os.environ.get("LOCAL_MEDIA_MOUNT", "/media")

        # SABnzbd's own container path for its completed-downloads
        # folder, and this sidecar's own local mount of that same
        # physical share -- same reasoning as local_media_mount above,
        # just for the download-staging share (2026-09-19, see
        # download_sources.SabnzbdSource).
        self.sabnzbd_downloads_root_path = os.environ.get("SABNZBD_DOWNLOADS_ROOT_PATH", "/downloads")
        self.local_downloads_mount = os.environ.get("LOCAL_DOWNLOADS_MOUNT", "/downloads")

        # Per-studio/performer delay between StashDB queries during a scan --
        # same rationale as release_watcher.py's --delay default (be a
        # polite, low-rate API client, not a burst).
        self.stashdb_query_delay = float(os.environ.get("STASHDB_QUERY_DELAY", "0.3"))

        self.scan_interval_hours = float(os.environ.get("SCAN_INTERVAL_HOURS", "6"))
        self.safety_sweep_interval_hours = float(os.environ.get("SAFETY_SWEEP_INTERVAL_HOURS", "24"))

        # Prowlarr (2026-09-20) -- investigating whether this sidecar
        # could hand a release's search off to Prowlarr directly instead
        # of routing through Whisparr's own `GET /release?movieId=X`
        # (user's own request -- they already run Sonarr/Radarr against
        # the same Prowlarr instance). Still just env-fallback plumbing
        # here (config.py's usual pattern) -- routers/settings.py's
        # DEFAULTS now exposes real `prowlarr_url`/`prowlarr_api_key`
        # Settings-page fields on top of this, added once the
        # Connections tab's ARR-style redesign landed (own TODO item,
        # done first on purpose so Prowlarr didn't get a one-off static
        # field prejudging that layout). Still NOT wired into
        # indexer_search.py's own candidate selection -- that's its own
        # separate design item.
        self.prowlarr_url = os.environ.get("PROWLARR_URL", "").rstrip("/")
        self.prowlarr_api_key = os.environ.get("PROWLARR_API_KEY", "")


settings = Settings()
