"""Size / resolution estimates from the scene length and the codec named in a release name (v0.231.0)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import torrent_estimate as te

fails = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

MB = 1e6
D = 17 * 60  # a 17 minute scene

check("codec: plain mp4 is h264", te.codec_of("Studio.20.10.01.Name.XXX.MP4-GRP") == "h264")
check("codec: hevc / h265 / x265", all(te.codec_of(f"Studio.Name.{x}.MP4-GRP") == "hevc" for x in ("HEVC", "h265", "x265", "H.265")))
check("codec: xvid / divx", te.codec_of("Name.XviD-GRP") == "mpeg4" and te.codec_of("Name.DivX.avi") == "mpeg4")
check("codec: mpeg2", te.codec_of("Name.MPEG2.VOB") == "mpeg2")
check("codec: 'x2650' style words do not count", te.codec_of("Name.hevcfan.MP4") == "h264")

lo, hi = te.expected_range(D, "h264")
check("h264 17 min: about 190 MB - 1.3 GB", 180 * MB < lo < 200 * MB and 1.2e9 < hi < 1.3e9)
lo2, hi2 = te.expected_range(D, "hevc")
check("hevc range is about 40 % smaller", abs(hi2 / hi - 0.6) < 0.01 and abs(lo2 / lo - 0.6) < 0.01)
check("no length -> no range / no verdict", te.expected_range(None) is None and te.size_fit(500 * MB, None) is None)
check("a length under 10 s counts as none", te.size_fit(500 * MB, 9) is None)
check("no size -> no verdict", te.size_fit(None, D) is None and te.size_fit(0, D) is None)

check("500 MB fits 17 min", te.size_fit(500 * MB, D) is True)
check("30 MB is too small (mislabelled)", te.size_fit(30 * MB, D) is False)
check("3 GB is too big for 17 min of h264", te.size_fit(3e9, D) is False)
check("the same 3 GB is even worse as hevc", te.size_fit(3e9, D, "hevc") is False)
check("mpeg2 allows more: 1.6 GB fits, not as h264", te.size_fit(1.6e9, D, "mpeg2") is True and te.size_fit(1.6e9, D, "h264") is False)

check("estimate: 2.5 Mbps -> 480", te.estimate_resolution(2.5e6 / 8 * D, D) == 480)
check("estimate: 4 Mbps -> 720", te.estimate_resolution(4e6 / 8 * D, D) == 720)
check("estimate: 7 Mbps -> 1080", te.estimate_resolution(7e6 / 8 * D, D) == 1080)
check("estimate: hevc is judged at its h264 equivalent (3.2 Mbps hevc -> 1080 range border)", te.estimate_resolution(3.2e6 / 8 * D, D, "hevc") in (720, 1080))
check("estimate: a size that does not fit gives none", te.estimate_resolution(30 * MB, D) is None)
check("estimate: unknown length gives none", te.estimate_resolution(500 * MB, None) is None)

check("describe: empty when it fits", te.describe(500 * MB, D) == "")
check("describe: names the length and sizes", "does not fit 17:00" in te.describe(3e9, D) and "3.00 GB" in te.describe(3e9, D))
print("\nALL OK" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
