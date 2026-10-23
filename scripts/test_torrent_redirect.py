"""A torrent link that redirects to a magnet link (Prowlarr's download URL does) is added as that magnet link, not an InvalidSchema error (v0.230.1)."""
import http.server
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import qbittorrent_client as q

fails = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

HASH = "a" * 40
MAGNET = f"magnet:?xt=urn:btih:{HASH}&dn=x"
TORRENT = b"d4:infod6:lengthi1e4:name1:x12:piece lengthi16384e6:pieces20:" + b"0" * 20 + b"ee"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/to-magnet":
            self.send_response(302); self.send_header("Location", MAGNET); self.end_headers()
        elif self.path == "/hop":
            self.send_response(302); self.send_header("Location", "/to-magnet"); self.end_headers()
        elif self.path == "/to-file":
            self.send_response(301); self.send_header("Location", "/file.torrent"); self.end_headers()
        elif self.path == "/file.torrent":
            self.send_response(200); self.end_headers(); self.wfile.write(TORRENT)
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, *a): pass


srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"

class Resp:
    text = "Ok."
sent = []
c = q.QbittorrentClient("http://qbit.invalid")
c.ensure_category = lambda *a, **k: None
c._call = lambda method, path, data=None, files=None, **k: (sent.append((path, dict(data or {}), files)), Resp())[1]

check("redirect to a magnet link -> added as magnet", c.add(base + "/to-magnet") == HASH and sent[-1][1]["urls"] == MAGNET and sent[-1][2] is None)
check("two hops, then a magnet", c.add(base + "/hop") == HASH)
check("redirect to a .torrent file is followed and uploaded", c.add(base + "/to-file") == q.torrent_info_hash(TORRENT) and sent[-1][2] is not None)
try:
    c.add(base + "/missing"); err = ""
except q.QbittorrentError as e:
    err = str(e)
check("404 still reported as HTTP error", "HTTP 404" in err)
print("\nALL OK" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
