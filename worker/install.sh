#!/bin/sh
# Installs the systemd user service for the encode worker and the KDE Plasma 6 widget (worker/plasmoid). Re-run after changing the settings below.
#   worker/install.sh --sidecar http://192.168.1.10:6961 --map /downloads=/mnt/nas/download/SABnzbd_d/complete
# Then: right-click the panel / desktop > Add Widgets > "SRM Encode Worker". The service is NOT enabled at login: start it from the widget.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
SIDECAR=""; MAPS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --sidecar) SIDECAR=$2; shift 2 ;;
    --map) MAPS="$MAPS --map $2"; shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
[ -n "$SIDECAR" ] || { echo "need --sidecar http://HOST:6961 (and one --map REMOTE=LOCAL per shared folder)" >&2; exit 2; }
mkdir -p "$HOME/.config/systemd/user"
sed -e "s|@PYTHON@|$(command -v python3)|" -e "s|@WORKER@|$HERE/srm_encode_worker.py|" -e "s|@SIDECAR@|$SIDECAR|" -e "s|@MAPS@|$MAPS|" \
  "$HERE/systemd/srm-encode-worker.service" > "$HOME/.config/systemd/user/srm-encode-worker.service"
systemctl --user daemon-reload
kpackagetool6 --type Plasma/Applet --upgrade "$HERE/plasmoid/org.srm.encodeworker" >/dev/null 2>&1 || kpackagetool6 --type Plasma/Applet --install "$HERE/plasmoid/org.srm.encodeworker"
echo "installed. Add the widget: Add Widgets > SRM Encode Worker; start the worker with its Start button."
