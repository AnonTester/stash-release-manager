"""What a release of a scene of a given length should weigh (v0.231.0).

Torrent names often carry no resolution, and a name can lie. The scene's length (from the provider), the codec named in the release name
and the size of the release tell whether the size fits and, when the name says nothing, roughly which resolution it is. All of it is an
estimate: the bands below are plain constants to be tuned, and a result is only ever used to rank a candidate (never to drop it).
"""
import re

MIN_DURATION_SEC = 10  # a provider length under this counts as none (same rule as duration_check.MIN_PROVIDER_SEC)
TOLERANCE = 0.25       # the band is widened by this at both ends before a size counts as "does not fit"

# Mbps range of a normal release per codec family (h264 is the base, hevc about 40 % smaller).
BITRATE_MBPS = {
    "h264": (2.0, 8.0),
    "hevc": (1.2, 4.8),
    "mpeg4": (1.5, 6.0),   # xvid / divx
    "mpeg2": (3.0, 12.0),
    "wmv": (1.5, 6.0),
}

# Bitrate (Mbps, h264 equivalent) at which a release is taken for 720p / 1080p; below the first it is taken for 480p.
RES_BREAKS_MBPS = ((3.2, 480), (5.5, 720), (float("inf"), 1080))

_HEVC_RE = re.compile(r"(?:^|[^a-z0-9])(?:hevc|h[. ]?265|x[. ]?265)(?:[^a-z0-9]|$)", re.IGNORECASE)
_MPEG4_RE = re.compile(r"(?:^|[^a-z0-9])(?:xvid|divx)(?:[^a-z0-9]|$)", re.IGNORECASE)
_MPEG2_RE = re.compile(r"(?:^|[^a-z0-9])(?:mpeg-?2|mpg|vob|dvd-?rip)(?:[^a-z0-9]|$)", re.IGNORECASE)
_WMV_RE = re.compile(r"(?:^|[^a-z0-9])wmv(?:[^a-z0-9]|$)", re.IGNORECASE)


def codec_of(title):
    """'hevc' / 'mpeg4' / 'mpeg2' / 'wmv' when the release name says so, else 'h264' (the assumption for mp4 / mkv)."""
    t = title or ""
    if _HEVC_RE.search(t):
        return "hevc"
    if _MPEG4_RE.search(t):
        return "mpeg4"
    if _MPEG2_RE.search(t):
        return "mpeg2"
    if _WMV_RE.search(t):
        return "wmv"
    return "h264"


def expected_range(duration_s, codec="h264"):
    """(min_bytes, max_bytes) a release of this length should have, tolerance included; None without a usable length."""
    if not duration_s or duration_s < MIN_DURATION_SEC:
        return None
    lo, hi = BITRATE_MBPS.get(codec, BITRATE_MBPS["h264"])
    return (duration_s * lo * 1e6 / 8 * (1 - TOLERANCE), duration_s * hi * 1e6 / 8 * (1 + TOLERANCE))


def size_fit(size, duration_s, codec="h264"):
    """True = the size fits the length and codec, False = clearly too small / too big (probably mislabelled), None = cannot tell."""
    rng = expected_range(duration_s, codec)
    if rng is None or not size:
        return None
    return rng[0] <= size <= rng[1]


def bitrate_mbps(size, duration_s):
    if not size or not duration_s or duration_s < MIN_DURATION_SEC:
        return None
    return size * 8 / duration_s / 1e6


def estimate_resolution(size, duration_s, codec="h264"):
    """480 / 720 / 1080 judged from the bitrate (a hevc file is compared at its h264 equivalent), None when it cannot be told or the size
    does not fit the band at all."""
    rate = bitrate_mbps(size, duration_s)
    if rate is None or size_fit(size, duration_s, codec) is not True:
        return None
    if codec != "h264":
        base, own = BITRATE_MBPS["h264"], BITRATE_MBPS.get(codec, BITRATE_MBPS["h264"])
        rate = rate * (base[1] / own[1])
    for limit, resolution in RES_BREAKS_MBPS:
        if rate < limit:
            return resolution
    return None


def describe(size, duration_s, codec="h264"):
    """Short text for the search log, e.g. 'size does not fit 17:40 (3.1 GB at 23.4 Mbps, expected 0.3-1.3 GB)'; '' when it fits or is unknown."""
    if size_fit(size, duration_s, codec) is not False:
        return ""
    lo, hi = expected_range(duration_s, codec)
    t = int(round(duration_s))
    length = f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"
    return (f"size does not fit {length} ({size / 1e9:.2f} GB at {bitrate_mbps(size, duration_s):.1f} Mbps, "
            f"expected {lo / 1e9:.2f}-{hi / 1e9:.2f} GB as {codec})")
