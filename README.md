<p align="center"><img src="assets/icon-256.png" width="128" alt="StashReleaseManager icon"></p>

# StashReleaseManager

A self-hosted add-on for [Stash](https://github.com/stashapp/stash): it watches the performers and studios you follow, shows which scenes and movies are released and which you are missing, finds them on your indexers, hands them to your download client and files them into your library.

Two parts:

* **sidecar** (`api/`, Docker): database, provider lookups (StashDB, ThePornDB), indexer search, download client hand-off, import and clean-up.
* **plugin** (`plugin/`): the pages inside Stash (Releases, Activity, Maintenance, Settings ...).

See [INSTALL.md](INSTALL.md) for installation, the first-start setup guide, moving to another machine and updating.

Requirements: Docker with Compose v2, Stash, a StashDB account, a search backend (Prowlarr or Newznab indexers) and a download client (SABnzbd; qBittorrent is supported but less tested). Whisparr is optional.

Status: used daily by its author, but expect rough edges. Torrent support and further metadata providers need more testing. Use at your own risk; make backups of your library and database first (Settings > General > Backup & move).

This project contains no media and no explicit content; it only manages metadata and files you already have the right to hold. You are responsible for following the law and the terms of the services you connect.

License: GNU AGPL-3.0, see [LICENSE](LICENSE). Copyright (c) 2026 northwindlab.
