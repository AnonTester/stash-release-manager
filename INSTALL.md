# StashReleaseManager -- installation

StashReleaseManager has two parts that work together:

* the **sidecar**, a small Docker container (this folder) that keeps the database, talks to StashDB / ThePornDB / your indexers / your download client and does the work in the background;
* the **plugin** for Stash (the `plugin/` folder), which gives you the pages (Releases, Activity, Maintenance, Settings ...) inside Stash.

Nothing else is needed: no Whisparr (optional), no extra database.

## What you need

| | |
|---|---|
| Docker with Compose v2 | on the machine that is to run the sidecar (a NAS next to Stash is the usual choice) |
| Stash | with the plugin folder writable (Docker or not) |
| A StashDB account, set up as a stash-box endpoint in Stash | Stash > Settings > Metadata Providers > Stash-box endpoints (the plugin reads the key from there; StashDB > your user page > API key if you have to enter it by hand) |
| Optional: ThePornDB (and other stash-boxes such as JAVStash, FansDB) | for ThePornDB releases and movies; also set up in Stash, the plugin takes the keys from there |
| A search backend | Prowlarr, or one or more Newznab indexers (entered in the plugin's Settings) |
| A download client | SABnzbd (usenet); qBittorrent (torrent) is supported but less tested, see "Torrents (optional)" |
| Optional: Whisparr | only if you want it to keep a second record of your library |

The sidecar must be able to **see your library files** (it moves finished downloads into the library) and the **finished downloads folder**. If Stash and the sidecar run on the same machine this is just two more mounts (step 2).

## 1. Get the files

Copy this whole folder to the machine that runs Docker, e.g. to `/srv/docker/StashReleaseManager`. (git clone, or unpack a ZIP -- the folder must contain `Dockerfile`, `api/` and `plugin/`.)

## 2. Create the compose file

```
cp docker-compose.example.yml docker-compose.yml
```

Edit the two **CHANGE** lines in `docker-compose.yml`:

* `/path/to/your/library:/media` -- the folder that holds your library. It must be the same files that Stash has. If Stash runs in Docker with `- /some/path:/data`, use `/some/path` here.
* `/path/to/sabnzbd/complete:/downloads` -- the folder where SABnzbd puts finished downloads.

Two cases that need one more line (both are commented in the file):

* Stash sees your library under another path than `/data` (e.g. `/library`): set `WHISPARR_ROOT_FOLDER_PATH=/library/`.
* You mount the library somewhere else than `/media` inside the container: set `LOCAL_MEDIA_MOUNT`.

If the library is a network share (NFS/SMB) that is mounted on the machine, make sure it is mounted **before** the container starts, or restart the container after a reboot. A container that started before its mount was ready sees an empty folder.

## 3. Start the sidecar

```
docker compose up -d --build
```

The first build takes a few minutes. Then check:

```
curl http://localhost:6961/health        ->  {"status":"ok"}
docker compose logs --tail 30
```

A fresh installation prints a few lines like "scheduled scan waits: not entered yet ..." -- that is normal, the jobs start by themselves once the entries in step 5 are made. There must be no "Traceback" lines.

(In Portainer: *Stacks > Add stack > Web editor*, paste your `docker-compose.yml`. Portainer cannot build from a local folder, so build on the machine with `docker compose build` first, or use `docker compose` from the command line.)

## 4. Install the plugin in Stash

1. Copy the **contents of the `plugin/` folder** (all `.js`, `.css`, `.yml`, `.py` files, not `__pycache__`) into a folder `stash-release-manager` inside Stash's plugin directory: `<Stash config folder>/plugins/stash-release-manager/`. (Stash in Docker: the folder you mount as `/root/.stash`.)
2. In Stash: *Settings > Plugins > Reload plugins*. "StashReleaseManager" appears in the list.
3. Open it there and fill in its three settings:
   * **Sidecar API URL** -- where your **browser** reaches the sidecar, e.g. `http://192.168.1.10:6961`. Not `localhost` unless the browser runs on the same machine.
   * **Stash URL** -- this Stash as **the sidecar** reaches it, e.g. `http://192.168.1.10:19999`.
   * **Stash API key** -- Stash > Settings > Security (leave empty if your Stash has no login).
4. Reload the Stash page. A box icon (**Release Manager**) appears in the top bar next to Statistics; under *Settings > General > Plugin menu* you can add further plugin pages to its drop-down.

## 5. First entries (in the plugin, Settings)

The **setup guide** opens by itself the first time you open *Release Manager* while something required is missing (and any time under *Settings > General > Setup guide*). It walks through the steps below one at a time, with the same fields as the Settings pages; what is done is shown with a tick. **Later** closes it for now (a reminder stays on the Releases page). The steps, if you prefer to do them by hand under *Release Manager > Settings*:

1. **Connections:** the **StashDB API key** (required), optionally the **ThePornDB API key**, **Prowlarr** (URL and key) and **Whisparr** if you use them. Further down on the same tab, **Download Clients**: add your SABnzbd (URL and API key).
2. **Search & Quality:** the **Search backend** (Prowlarr, or your own Newznab **Indexers** entered on the same tab) and a **Quality profile** (which resolutions you accept).
3. **Import & Naming:** under *File Import* the two SABnzbd paths -- "SABnzbd's own downloads path" is the folder as **SABnzbd** calls it (its *complete* folder inside its container), "This sidecar's local mount of it" is `/downloads` -- and, under *Naming*, how imported files are named and sorted.
4. **General:** *Content* (scenes, movies, or both) and the *Watchlist source* (your Stash favorites, or a tag).
5. Then the **Watchlist** page: which performers / studios are followed.

Optional, **off by default**: *Settings > Import & Naming > Organized hook* lets you move/rename a scene by marking it *Organized* in Stash (switch it on there; start with *Preview only*). Before using any moving function, look at *Import & Naming > Safety check*: another plugin that renames files (renamerOnUpdate ...) must not be enabled at the same time.

**Next: [FIRST_STEPS.md](FIRST_STEPS.md)** -- a walk through the pages with screenshots, from choosing who to follow to the first download.

Run *Release Manager > Releases > Run scan now* once. The scheduled jobs (scan every few hours, searches, import check) run by themselves from now on; their intervals are in Settings.

Keys and URLs are stored in the sidecar's database in the `data` folder. They are never part of an export (see below). A value you type yourself is kept; a key that is empty is filled from Stash. *Update keys from Stash* (setup guide, *Settings > Metadata Providers*) replaces the keys set here with the ones Stash holds. `STASH_URL` / `STASH_API_KEY` in the container's environment still work as a fallback.

## Stash behind an HTTPS reverse proxy (optional)

Only needed when you open Stash through an **HTTPS** address (a reverse proxy such as Nginx Proxy Manager, Caddy or Traefik, possibly with a login such as Authelia). Over plain HTTP on your LAN nothing has to be done.

**What goes wrong without it.** The plugin's pages get live updates over a WebSocket that the browser opens to the sidecar. A page loaded over HTTPS may not open a plain `ws://` connection (mixed content), so the browser console shows `WebSocket connection to 'ws://...:6961/events/ws' failed` about every five seconds. Nothing else breaks: the pages fall back to polling every ten seconds, and all other calls go through Stash's server side. The sidecar address in the plugin setting stays the one **Stash** reaches it by (e.g. `http://<sidecar-host>:6961`).

**What the plugin does about it.** When Stash is opened over HTTPS and the *Sidecar API URL* starts with `http://`, the plugin opens the socket on the address you opened Stash with: `wss://stash.example.org/events/ws`. Your proxy only has to forward that one path to the sidecar.

Forward **only `/events/ws`**. The sidecar has no login of its own: do not publish the rest of its API. The stream carries scene ids, statuses and progress numbers, never keys. (Dropping an export file on *Settings > General > Backup & move* uses a second path, `/transfer/upload`; it then works from a page opened over HTTP on your LAN, which is the safer default.)

### Nginx Proxy Manager, no login in front of Stash

On the Stash proxy host: *Websockets Support* on, then *Custom locations > Add location*:

| Field | Value |
|---|---|
| Define location | `/events/ws` |
| Scheme | `http` |
| Forward Hostname / IP | the sidecar's address, as the proxy reaches it |
| Forward Port | `6961` (the host port in your `docker-compose.yml`) |

### Nginx Proxy Manager with Authelia (or an Access List)

Do **not** use the *Custom locations* tab. NPM puts the host's *Access List* (HTTP basic authentication) on every custom location it generates; a WebSocket cannot answer that, and the browser console shows `401 Unauthorized` with `WWW-Authenticate: Basic realm="Authorization required"` although you are logged in. Add the location by hand to the host's **Advanced** tab instead, next to the `location /` you already have there, with the same includes:

```
include /snippets/authelia-location.conf;

location / {
    include /snippets/proxy.conf;
    include /snippets/websocket.conf;
    include /snippets/authelia-authrequest.conf;
    proxy_pass $forward_scheme://$server:$port;
}

location /events/ws {
    include /snippets/proxy.conf;
    include /snippets/websocket.conf;
    include /snippets/authelia-authrequest.conf;
    proxy_pass http://<sidecar-host>:6961;
}
```

The `/snippets/...` files are the ones from Authelia's own guide for NPM; if yours are named differently, use the same files your `location /` includes. Do not create a *Custom location* with the same path as well: nginx rejects two blocks for one path and the host shows as broken.

`proxy_pass` has no path part on purpose, so the request path `/events/ws` reaches the sidecar unchanged.

### Plain nginx

```
location /events/ws {
    proxy_pass http://<sidecar-host>:6961;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 3600s;
    # the same login directive(s) as your location / (auth_request ..., auth_basic ...)
}
```

### Check

* Browser: open *Release Manager* with the console open. The reconnect errors stop; a request to `/events/ws` shows status `101`.
* Command line, without a session: `curl -i https://stash.example.org/events/ws` must answer with your login redirect (`302`) or `401`, not `404` or `502`.
* The sidecar log shows `"WebSocket /events/ws" [accepted]`.

## Torrents (optional)

Usenet and torrents use the same search and import pipeline; torrents need two more things: a **torrent indexer** and **qBittorrent** as a download client. **Status: the least tested part** -- it follows the conventions of Radarr / Whisparr, but it has had little use against real trackers. Please report what does not work.

1. **Download client:** *Settings > Connections > Download Clients > Add*, type **qBittorrent (Torrent)**: URL of its web UI, user name and password, optionally a category. Leave the other fields at their defaults.
2. **Folders:** the sidecar must see the folder qBittorrent writes to. Mount it into the sidecar container (like `/downloads`) and enter the pair under *Download folder in qBittorrent* (the path as qBittorrent calls it) and *as this program sees it* (the path in the sidecar). Both empty = the usenet downloads folder. Finished torrents are **hard-linked** into the library (copied if that is not possible) so they can keep seeding; a hard link only works when the download folder and the library are on the **same file system**.
3. **Indexers**, one of two ways:
   * **Prowlarr** as search backend (*Search & Quality*): it returns usenet and torrent results; results whose protocol has no enabled download client are dropped. Add your torrent trackers in Prowlarr.
   * **Direct indexers:** *Settings > Search & Quality > Indexers*, type **Torznab (Torrent)** -- the Torznab URL and API key come from Prowlarr or Jackett (one entry per tracker). *Minimum seeders* (default 1) drops dead torrents; *Seed ratio* / *Seed time* empty = qBittorrent's own limits apply.
4. **Optional, faster import:** in qBittorrent *Tools > Options > Downloads > Run external program on torrent finished*: `/config/srw_notify.sh "%I"` with the script `qbittorrent/srw_notify.sh` from this project (set `SIDECAR_URL` in it, `chmod +x`). Without it the sidecar finds finished torrents on its next poll.
5. **After seeding:** under the download clients list (*Connections > Download Clients*), the part *Torrents: after seeding*, you can let the sidecar remove a finished torrent from qBittorrent once its seeding goal is reached (off by default; only torrents it grabbed itself).

When both a usenet and a torrent result are found for the same release, usenet is preferred (like the *arr default).

## Moving to another machine (or a fresh install) with your data

On the **old** installation: *Settings > General > Backup & move > Export*. Pick what to take (watchlist, releases, movies, decisions, settings, check results; the history only if you want it). A ZIP appears in `data/exports/` (and as a download link).

Before you import: **stop the old sidecar's jobs** -- `docker compose down` there, or, if you want to keep it as a development copy, start it with `SRM_DEV_MODE=1` (see below) -- once its running downloads have finished and been imported -- a download that is still running is not carried over (its job belongs to the old download client), and two sidecars working on the same Stash would search and grab twice.

On the **new** one (after steps 1 to 5, **before** you scan or search anything -- an import only goes into an installation that has no releases or movies yet; the watchlist the setup guide fills from Stash by itself does not count. In the setup guide's last step choose **Later**, not *Run the first scan now*):

1. *Settings > General > Backup & move*: drop the ZIP on the dashed area (or **Choose file…**). It is uploaded from your browser into `data/imports/` of the new installation. (You can also just copy the file into that folder yourself.)
2. *Files > Check import (dry run)* next to the uploaded file -- it only counts and tells you what blocks.
3. *Import now*. A backup of the database is made first; any problem rolls everything back.
4. `docker compose restart` once.

URLs, API keys and local paths are not in the export: on the new installation the Stash key and the StashDB / ThePornDB / stash-box keys come from Stash again, the rest you enter in step 5.

Then point the plugin's **Sidecar API URL** (Stash > Settings > Plugins) at the new sidecar and reload the browser page.

After the restart the new sidecar takes up the schedule of the old one (the times of the last runs are part of the export) and searches the wishlist again; releases that were downloading at the time of the move are searched again, and a release that is already in SABnzbd's history is not fetched twice.

## A second copy for development or as a standby

Two sidecars working on the same Stash, indexers and download client would search and grab twice. If you keep an extra copy (to develop, to try an update, or while you move), start it with **`SRM_DEV_MODE=1`** (an `environment:` line in its compose file; the example has it commented). Such a copy starts **no** scan, search, import, sync or clean-up job and no Stash event connection; the web pages and manual actions still work, and the plugin header shows "Development mode: no background jobs". `curl http://HOST:6961/health` then says `"dev_mode": true`.

The switch is deliberately an environment variable and not a setting: settings travel with an export / import, this must not. Do **not** click manual actions (Run scan now, Search ...) in a development copy that points at your real services unless you mean it.

## Updating

1. Replace the project folder's files with the new version (keep `data/` and your `docker-compose.yml`).
2. `docker compose up -d --build`
3. Copy the new `plugin/` files over the old ones in Stash's plugin folder and *Reload plugins*; reload the browser page.

`CHANGELOG.md` lists the versions. Before an update, a copy of `data/release_watcher.sqlite3` is a sensible backup (or use *Export*).

## If something does not work

| Symptom | What to check |
|---|---|
| The plugin pages stay empty or show "sidecar not reachable" | *Sidecar API URL* must be reachable **from your browser**; open it with `/health` appended. |
| The sidecar log says it waits for the Stash URL or API key | Open the Release Manager page once (the address you use for Stash is taken over), then check the *Stash* step of the setup guide. With a login in Stash: generate an API key under *Stash > Settings > Security*. |
| The Stash header light stays red although the URL is right | The sidecar reaches Stash under another address than your browser (reverse proxy, Docker network name): enter that one in the *Stash* step of the setup guide. |
| Console: `WebSocket connection to 'ws://...' failed` every few seconds, Stash opened over HTTPS | Mixed content: forward `/events/ws` through your reverse proxy, see *Stash behind an HTTPS reverse proxy*. |
| `wss://.../events/ws` answers `401` with `Basic realm="Authorization required"` (Nginx Proxy Manager) | The host's Access List was applied to a custom location: add the location in the host's *Advanced* tab instead, see the same section. |
| "Source file missing" when importing, or an empty `/media` | The library mount was not ready when the container started: mount it, then `docker compose restart`. |
| "Stale file handle" on an NFS library after the file server restarted | Stop the container, unmount and remount the share, start the container. |
| StashDB / ThePornDB are not reachable although the network works | `docker compose restart` (the container keeps an old DNS answer). |
| Nothing is downloaded | *Settings > Search & Quality*: backend and quality profile; *Activity* shows what each release is waiting for. |
