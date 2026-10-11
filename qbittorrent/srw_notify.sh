#!/bin/sh
# qBittorrent "Run external program on torrent finished" for stash-release-manager (v0.135.0).
#
# Tells the sidecar right away that a torrent finished, so it imports the files immediately instead of waiting for its next poll (the poll
# stays as a safety net). Same endpoint the SABnzbd script uses; the torrent's info hash is the job id. Torrents this sidecar did not grab are
# ignored by the sidecar.
#
# In qBittorrent: Tools > Options > Downloads > "Run external program on torrent finished" (tick it), enter:
#   /config/srw_notify.sh "%I"
# (copy this file to a folder qBittorrent can run it from, `chmod +x` it, and set SIDECAR_URL below to the sidecar as the qBittorrent
# container reaches it). Always exits 0: a sidecar that is down must never disturb qBittorrent.
SIDECAR_URL="http://192.168.1.10:6961"
HASH="$1"
[ -n "$HASH" ] || { echo "srw_notify: no hash given"; exit 0; }
curl -s -m 10 -X POST -H 'Content-Type: application/json' -d "{\"nzo_id\":\"$HASH\",\"status\":\"0\"}" \
  "$SIDECAR_URL/file-import/download-complete" >/dev/null 2>&1 || echo "srw_notify: sidecar not reachable, it will pick this up on its next poll"
exit 0
