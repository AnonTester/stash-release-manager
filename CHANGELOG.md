# Changelog

Short summary of what each version added; versions follow SemVer.

Version rules (from 0.199.0 on; before, almost every change raised the MINOR number): PATCH = a fix or a refinement of something that exists;
MINOR = a new capability a user can use, or a change that needs the user to act; MAJOR = something that worked breaks. 1.0.0 comes when the
install guide has been proven on other machines. Documentation only gets no version.

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
