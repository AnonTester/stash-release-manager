"""PLUGIN_VERSION in the core JS must equal the plugin yml version and the sidecar's SIDECAR_VERSION."""
import re, sys
from pathlib import Path
root = Path(__file__).resolve().parent.parent
js = re.search(r"PLUGIN_VERSION = '([^']+)'", (root / "plugin/stash-release-manager-core.js").read_text()).group(1)
yml = re.search(r"^version:\s*(\S+)", (root / "plugin/stash-release-manager.yml").read_text(), re.M).group(1)
side = re.search(r'SIDECAR_VERSION = "([^"]+)"', (root / "api/version.py").read_text()).group(1)
print(js, yml, side)
sys.exit(0 if js == yml == side else 1)
