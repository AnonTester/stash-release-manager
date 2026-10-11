"""Quality detection for a downloaded file, once this sidecar imports
files itself instead of trusting Whisparr's own grab-time quality
metadata (Milestone 2, 2026-09-17).

Two parts, mirroring Whisparr's own "Quality Full" concept
(`{source}-{resolution}`, e.g. "WEBDL-1080p"):
- resolution: read directly from the actual file via `ffprobe`
  (Whisparr's own "Analyze video files" setting does the same kind of
  direct-file read for its own {MediaInfo ...} tokens).
- source: guessed from the ORIGINAL downloaded filename/folder name
  via a keyword regex -- the same handful of source tags indexers
  themselves use in release names, which is genuinely not recoverable
  from the file's own bytes.
"""
import json
import logging
import re
import subprocess

logger = logging.getLogger(__name__)

# Ordered so a more specific tag (e.g. "webdl") is tried before a
# looser one that could also match part of it -- checked top to
# bottom, first match wins.
SOURCE_PATTERNS = [
    (re.compile(r"web[-. ]?dl", re.IGNORECASE), "WEBDL"),
    (re.compile(r"web[-. ]?rip", re.IGNORECASE), "WEBRip"),
    # "Bluray", not "BluRay": Whisparr's spelling, as in the library's names.
    (re.compile(r"blu[-. ]?ray|bdrip|bd25|bd50", re.IGNORECASE), "Bluray"),
    (re.compile(r"hdtv", re.IGNORECASE), "HDTV"),
    (re.compile(r"dvdrip|dvd-rip", re.IGNORECASE), "DVD"),
]

RESOLUTION_BUCKETS = [
    (2160, "2160p"),
    (1440, "1440p"),
    (1080, "1080p"),
    (720, "720p"),
    (480, "480p"),
]


# A release name without any source tag (2026-09-28): WEBDL, like Whisparr
# names them -- the library has 5,433 "WEBDL-1080p" files, almost all from
# untagged site releases ("Site.26.09.22.Name.XXX.1080p.MP4-GRP"); the
# importer used to write "Unknown" there (49 files).
DEFAULT_SOURCE = "WEBDL"


def guess_source(original_filename):
    for pattern, label in SOURCE_PATTERNS:
        if pattern.search(original_filename or ""):
            return label
    return DEFAULT_SOURCE


def probe_size(file_path, timeout=30):
    """(width, height) of the video stream, (None, None) on failure."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-select_streams", "v:0", file_path],
            capture_output=True, text=True, timeout=timeout,
        )
        streams = json.loads(result.stdout).get("streams") or []
        return (streams[0].get("width"), streams[0].get("height")) if streams else (None, None)
    except Exception:
        logger.exception("probe_size: ffprobe failed for %s", file_path)
        return None, None


def vr_label(width, height, is_vr=False):
    """VR frames (2:1 side-by-side or ~1:1 over-under/fisheye, at least
    2880 px wide) are named by their width class like the studios do --
    "6K", "8K" -- instead of being capped at "2160p" (2026-09-26: the first
    6K upgrade came out as "Unknown-2160p"). None for a normal 2D frame."""
    if not width or not height or width < 2880:
        return None
    ratio = width / height
    # A known VR scene gets its width class whatever the frame shape
    # (2026-09-28: 16:9 half-SBS VR came out as "Unknown-2160p").
    if not is_vr and not (1.9 <= ratio <= 2.1 or 0.9 <= ratio <= 1.1):
        return None
    for min_width, label in ((7600, "8K"), (6900, "7K"), (5700, "6K"), (4800, "5K"), (3800, "4K"), (2880, "3K")):
        if width >= min_width:
            return label
    return None


def probe_height(file_path, timeout=30):
    """The video stream's own height in pixels, via `ffprobe` --
    returns None on any failure (missing ffprobe binary, corrupt file,
    timeout) rather than raising, since a quality label is a naming
    nicety, not something worth failing the whole import over."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-select_streams", "v:0", file_path],
            capture_output=True, text=True, timeout=timeout,
        )
        data = json.loads(result.stdout)
        streams = data.get("streams") or []
        return streams[0].get("height") if streams else None
    except Exception:
        logger.exception("probe_height: ffprobe failed for %s", file_path)
        return None


def resolution_label(height):
    if not height:
        return "Unknown"
    for min_height, label in RESOLUTION_BUCKETS:
        if height >= min_height:
            return label
    # Below 480 is "480p" too (2026-09-28): the library's convention
    # (1,144 "WEBDL-480p" files, none "SD") -- Whisparr's lowest class.
    return "480p"


def detect_quality(file_path, original_filename, is_vr=False):
    """Returns a "Quality Full"-shaped label, e.g. "WEBDL-1080p" --
    never raises; both halves independently fall back to "Unknown" so
    a probe failure never blocks the import itself, only makes the
    resulting filename less descriptive."""
    source = guess_source(original_filename)
    width, height = probe_size(file_path)
    resolution = vr_label(width, height, is_vr) or resolution_label(height)
    return f"{source}-{resolution}"
