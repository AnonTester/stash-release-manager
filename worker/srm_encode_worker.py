#!/usr/bin/env python3
"""Encode worker for StashReleaseManager (v0.220.0): re-encodes the 2160p last-resort downloads on THIS machine instead of the NAS.

  python3 worker/srm_encode_worker.py --sidecar http://NAS:6961 --map /downloads=/mnt/nas/download/SABnzbd_d/complete

It asks the sidecar for waiting encodes (Settings > Quality > "Where to encode" = "Encode worker"), reads the download from the shared
folder, writes the 1080p copy next to it, and reports progress and the result. Switch the machine off and the jobs simply wait.
Needs ffmpeg + ffprobe and Python 3.9+ (standard library only). Uses the same encoder code as the sidecar (api/transcode.py), so Intel/AMD
hardware encoding (VAAPI, /dev/dri) works here when the machine has it; the settings (quality, format, CPU speed, hardware) come from the sidecar.

--map REMOTE=LOCAL  (repeatable) translates a path as the sidecar sees it (/downloads/...) into the folder on this machine.
"""
import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))
import transcode  # noqa: E402


def call(base, path, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base.rstrip("/") + path, data=data, headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def translate(path, maps):
    for remote, local in maps:
        if path == remote or path.startswith(remote.rstrip("/") + "/"):
            return local.rstrip("/") + path[len(remote.rstrip("/")):]
    return None


def run_job(base, name, job, maps):
    provider, stash_id = job["provider"], job["stash_id"]
    src, dst = translate(job["source"], maps), translate(job["dest"], maps)
    key = {"provider": provider, "stash_id": stash_id, "worker": name}
    if not src or not dst or not os.path.exists(src):
        call(base, "/encode-worker/done", {**key, "ok": False, "error": f"{job['source']} is not visible on this machine (check --map)"})
        return
    print(f"[{time.strftime('%H:%M:%S')}] encoding {os.path.basename(src)}", flush=True)
    last = [0.0]

    def progress(pct, speed, left):
        if time.time() - last[0] < 5:
            return
        last[0] = time.time()
        reply = call(base, "/encode-worker/progress", {**key, "percentage": pct, "speed_x": speed, "seconds_left": left, "method": method[0]})
        if not reply.get("ok"):
            raise transcode.StopEncode()

    method = [transcode.method_label(job["hw_mode"], job["codec"])]
    started = time.time()
    try:
        res = transcode.transcode(src, dst, quality=job["quality"], hw_mode=job["hw_mode"], progress=progress, codec=job["codec"], preset=job["preset"])
    except Exception as e:  # never leave a half-written file behind
        res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        transcode._remove(dst)
    if res.get("error") == "stopped":
        print("  the sidecar gave the job to someone else; stopped", flush=True)
        transcode._remove(dst)
        return
    body = {**key, "ok": res["ok"], "error": res.get("error"), "seconds": int(time.time() - started), "hw": res.get("hw", False), "codec": res.get("codec", job["codec"])}
    call(base, "/encode-worker/done", body)
    print(f"  {'done' if res['ok'] else 'failed: ' + str(res.get('error'))} after {body['seconds']} s ({method[0]})", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sidecar", required=True, help="the sidecar's address, e.g. http://192.168.1.10:6961")
    ap.add_argument("--map", action="append", default=[], metavar="REMOTE=LOCAL", help="path translation, e.g. /downloads=/mnt/nas/download/SABnzbd_d/complete")
    ap.add_argument("--name", default=socket.gethostname(), help="how this worker shows up on the Activity page (default: the host name)")
    ap.add_argument("--interval", type=int, default=30, help="seconds between asking for work (default 30)")
    ap.add_argument("--once", action="store_true", help="do at most one job, then exit")
    args = ap.parse_args()
    maps = []
    for m in args.map:
        if "=" not in m:
            ap.error(f"--map needs REMOTE=LOCAL, got {m!r}")
        maps.append(tuple(m.split("=", 1)))
    print(f"encode worker {args.name!r} asks {args.sidecar} every {args.interval} s", flush=True)
    while True:
        try:
            job = call(args.sidecar, f"/encode-worker/next?worker={urllib.request.quote(args.name)}").get("job")
            if job:
                run_job(args.sidecar, args.name, job, maps)
                if args.once:
                    return
                continue  # look for the next one right away
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"[{time.strftime('%H:%M:%S')}] sidecar not reachable: {e}", flush=True)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
