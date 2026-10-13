# Changelog

Short summary of what each version added; versions follow SemVer.

Version rules (from 0.199.0 on; before, almost every change raised the MINOR number): PATCH = a fix or a refinement of something that exists;
MINOR = a new capability a user can use, or a change that needs the user to act; MAJOR = something that worked breaks. 1.0.0 comes when the
install guide has been proven on other machines. Documentation only gets no version.

## 0.211.5
- Live updates (and the export upload) also work when Stash is opened over HTTPS behind a reverse proxy: the plugin then opens the socket on the page's own address (wss://<stash host>/events/ws) instead of the blocked ws:// one. INSTALL.md describes the proxy setup for Nginx Proxy Manager (with and without Authelia) and plain nginx. (Contributed in pull request #1.)

## 0.211.4
- Maintenance > Duration mismatch: lengths are shown as [h:]mm:ss, the badge shows the difference with the percentage in brackets, and a provider length under 10 seconds counts as no length. (Pull request #1.)

## 0.211.3
- Requests to Prowlarr wait up to 30 seconds (was 15). (Pull request #1.)

## 0.211.2
- A scene you already dismissed stays dismissed when another watched entry (performer, studio, parent studio) reports it later; before, it could show up in the Calendar again.

## 0.211.1
- Activity: movies that are wanted or failed get the "Search…" button (own search term + Grab), like scenes and the Movies page.

## 0.211.0
- Search page, By tag: tags can be excluded. Click a tag once = scenes must have it, twice = scenes must not have it, three times = off. Presets keep the exclusions.

## 0.210.0
- Search page, By tag: select several tags at once, a scene must carry all of them. The selection stays while you search for more tags.
- Tag combinations can be saved as presets (per provider).

## 0.209.3
- Dismiss (Calendar, List, late additions): a failed request now shows "Dismiss failed" instead of failing silently.

## 0.209.2
- Inline questions ("Add to wishlist? Yes / Cancel"): when the question does not fit on one line the text wraps and the two buttons stay together.

## 0.209.1
- Search page: on a scene marked "In library" the cover and the title open the scene in Stash; the separate button is gone.

## 0.209.0
- Search page: scenes that are already in Stash get "Re-add" and "Re-add + search". The scene goes back on the wishlist; after a verified download the new file replaces the old one (the old file goes to the recycle bin).
- Maintenance > Duration mismatch: lists library scenes whose file length differs from the provider's length by more than a threshold (default 5 %, shorter or longer), with Re-add per scene. Scenes without a provider length are not checked.

## 0.208.1
- Releases list: a release that came in through a watched studio but belongs to one of its sub-studios now shows the scene's own studio next to the watched one, so "Hide studio" is clearly about the sub-studio.

## 0.208.0
- Search page, ThePornDB: a "Movies" tab searches ThePornDB movies and puts one on the film wishlist ("Add to film wishlist"); a movie already there shows "on film wishlist".
- Search page: the tag search is now "By title | By tag" under Scenes and only offered for providers that can do it (StashDB and added stash-boxes, not ThePornDB).

## 0.207.2
- Search page: a scene marked "In library" has an "Open in Stash" button.

## 0.207.1
- Search page: a scene that Stash already has (a file under the same provider id) is marked "In library" and cannot be added again, also when this program never saw it before. If Stash does not answer, the search works as before.

## 0.207.0
- Search page: new way to search scenes by tag (StashDB and added stash-boxes): the term finds tags, a click lists the tag's newest scenes with Add / Add + search. ThePornDB cannot do it (its tag filter returns nothing).
- Search page: the image and title of every card open the item's page on the provider's website.

## 0.206.1
- Search page: the plugin waits up to 60 seconds (was 20) for a search, so a slow stash-box no longer ends in "Read timed out" while the sidecar is fine.

## 0.206.0
- Search page, scenes: a scene this program already knows is shown as such, also on a new search. On the wishlist: purple with "On Wishlist", "Search now" and "Dismiss"; imported: green; import failed: red; dismissed: grey with "Restore". After "Add" / "Add + search" the card changes at once.

## 0.205.6
- Search page: the cards of a page have the same height; scene images are 3:2, performers 2:3, studio logos sit whole in a square box. Missing images are a grey box of the same size, the buttons sit at the bottom of the card, long words wrap.

## 0.205.1
- Fix: "Possible match" on the wishlist no longer offers a release in a resolution the quality profile does not accept (e.g. 2160p while the profile takes 1080p / 720p).

## 0.205.0
- Two ways to put a release on the wishlist: "Add" only adds it (the scheduled search finds it later), "Add + search" also asks the indexers right away. A slow indexer no longer ends in a time-out error: the search runs in the background after 20 seconds, the result is in Activity / History, and a second click does not start another search.

## 0.204.3
- Compare / Review "Search via Stash scraper": ThePornDB is offered as Stash's stash-box (the same source as "Scrape with ..." in Stash) instead of the community scraper of that name, which failed on systems that cannot verify the certificate chain of its server.

## 0.204.2
- Maintenance: the number behind "Review: Identify Scene" (and the other links) follows the list while scenes are worked off.

## 0.204.1
- Review: Identify Scene: opening a single scene showed the word "null" above the buttons at the bottom. Fixed. A timeout of Stash's scraper now says that Stash itself is reachable and suggests pasting the scene's page address instead of a title search.

## 0.204.0
- Setup without copying, contributed by AnonTester: the plugin's only setting is the Sidecar API URL. The Stash API key and the StashDB / ThePornDB keys are read from Stash itself (the keys never pass the browser); the Stash URL is prefilled from the browser's address and confirmed in the setup guide. Other stash-boxes set up in Stash (FansDB, JAVStash ...) are offered as providers and created on request. "Update keys from Stash" overwrites typed keys on purpose.
- The download client form has a "Test connection" button that checks the details as typed (qBittorrent or SABnzbd) before saving; the old check no longer assumes SABnzbd.

## 0.203.1
- Fix: the setup guide's "Finish" step showed the word "null". New FIRST_STEPS.md with screenshots (documentation).

## 0.203.0
- Organized hook: scenes with a provider id in the Whisparr library folder (scenes/) are handled too, planned and moved like Maintenance > Organize library does for one scene.

## 0.202.0
- Maintenance > Organize library checks existing films against the movie templates (folder, file name, year, capitals, edition notes such as "(DVD + Blu-ray Combo)") and offers to fix the differences. The warning about other renamers only appears while the Organized hook is on and another renamer is enabled.

## 0.201.0
- Activity: "Search all stuck now" sends one targeted indexer query for every stuck scene, after asking.

## 0.200.0
- Adding a performer or studio to the watchlist can load its catalog (setting: off / ask / automatic); the catalog works for every stash-box (FansDB, JAVStash ...), not only StashDB. Each stash-box has its own query delay and favorites interval (default: StashDB's); favorites of other boxes are synced too.
- Download clients: "Take over from Prowlarr" prefills name, type and address from Prowlarr's own client.

## 0.199.0
- New metadata providers get different colours. Search page: wrapped scene cards no longer spill text into the neighbouring pill.

## 0.198.0
- The Organized hook has its own section with a switch (off by default) and a mode (preview only / move for real).

## 0.197.0
- Safety check for moving and renaming (Settings > Import & Naming > Safety check): finds another plugin that renames files on scene updates (renamerOnUpdate and relatives) and then refuses to move files unless you confirm; also checks that target folders are inside a Stash library, that the formats cannot give every file one name, the Whisparr scope, and whether moved files were moved again by something else.

## 0.196.0
- Settings > Import & Naming > Naming: the movie part is headed "Movies/Groups".

## 0.194.0
- The movie layout is a template (Settings > Import & Naming > Naming > Movies/Groups): folder and file format with the tokens {Movie Title}, {Movie Year}, {Part Number}, {Studio Title}, {Quality Full}, {Source}, {Resolution}. The defaults give the layout of earlier versions.

## 0.193.0
- Modular naming: optional parts `< >`, replace-words rules (text / word / regex), tag folders (you choose which tags get a folder; the list order is the priority), tokens {Release Year}, {Source}, {Resolution}, {Tag Folder}, and a live example under the format fields.

## 0.192.0
- File and folder names stay within the file system's 255-byte limit: only the title is shortened.

## 0.191.0
- "Organized" hook: marking a scene Organized in Stash can move/rename it (off / preview / on, default off).

## 0.190.0
- Import folder: grouped movie scenes (one Stash group with a ThePornDB movie link, or marked Organized) are placed into the film layout.

## 0.189.0
- A download rejected for its file (too small, unreadable) blocks only that release name, not its other encodes.

## 0.188.0
- An open search panel is no longer closed by live updates of the list.

## 0.187.0
- Prowlarr text searches no longer filter by category on the server (some indexers file results under sub-categories); the XXX range is filtered locally.

## 0.186.0
- "Add to Wishlist" no longer fails when Whisparr does not know a very new scene (the sidecar searches on its own).

## 0.185.0
- The top-bar entry is one icon among Stash's utility icons. With the separate [Plugin Menu](https://github.com/northwindlab/stash-plugin-menu) plugin installed, this plugin registers itself in that menu (with the activity dot); without it a plain icon is drawn.

## 0.183.0
- Missing from Catalog: filter by co-performer (text field with a list of the other performers in the scenes).

## 0.182.0
- Missing from Catalog: scene pairing filter (B/G, G/G, B/G/G ...) and a pairing badge per row.

## 0.181.0
- Review > Identify Scene: "Move to _UNMATCHED" also removes the review tag, so the scene leaves the list at once.

## 0.180.0
- Backup & move: upload an export file from the browser (drop area), over a WebSocket.

## 0.179.0
- First-start setup guide: walks through the connections, search, download client, quality and content once.

## 0.178.0
- `SRM_DEV_MODE=1`: a development copy that runs no background jobs.

## 0.177.0
- INSTALL.md and a generic compose example; quiet start of a fresh installation.

## 0.176.0
- No search when the provider lookup fails.

## 0.173.0 - 0.175.0
- Backup & move (export / import of watchlist, releases, movies, decisions, settings); fixes in the release identity check used by the blocklist.

## 0.165.0 - 0.172.0
- Per-page dismiss, scene-import readability check, Whisparr status on movie rows, "Dismiss & delete group", Maintenance lists, "Unidentified scenes" page with StashDB fingerprint check, Review fixes.

Earlier versions: the foundation (watchlist, scan, releases, calendar, search, hand-off, import, maintenance pages).
