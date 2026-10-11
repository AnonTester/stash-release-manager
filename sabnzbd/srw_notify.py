#!/usr/bin/env python3
"""SABnzbd post-processing script for stash-release-manager (2026-09-25).

Tells the sidecar right away that a SABnzbd job finished, so it can
import the file immediately instead of waiting for its next 15-minute
poll (which stays as a safety net). Set as the script of SABnzbd's
default category "*" -- runs after EVERY job; the sidecar ignores jobs
it didn't grab itself.

Install: copy to SABnzbd's script folder (Config > Folders > Scripts
Folder), then Config > Categories > "*" > Script = srw_notify.py.
SIDECAR_URL below must point at the sidecar as reachable from the
SABnzbd container.

Always exits 0: a sidecar that's down must never make SABnzbd mark a
finished download as failed. Standard library only (runs inside the
SABnzbd container's own Python).
"""
import json
import os
import sys
import urllib.request

SIDECAR_URL = "http://192.168.1.10:6961"


def main():
    nzo_id = os.environ.get("SAB_NZO_ID", "")
    status = os.environ.get("SAB_PP_STATUS", "")
    if not nzo_id:
        print("srw_notify: no SAB_NZO_ID, nothing to report")
        return
    body = json.dumps({"nzo_id": nzo_id, "status": status}).encode()
    req = urllib.request.Request(f"{SIDECAR_URL.rstrip('/')}/file-import/download-complete", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"srw_notify: sidecar answered {resp.read().decode()[:200]}")
    except Exception as e:
        print(f"srw_notify: could not reach sidecar ({type(e).__name__}), it will pick this up on its next poll")


if __name__ == "__main__":
    main()
    sys.exit(0)
