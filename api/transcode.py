"""Re-encode a 2160p download to 1080p before the import (v0.218.0).

When no release in the quality profile exists and a scene is older than `fallback_resolution_days`, the search may take a 2160p release
(indexer_search.fallback_resolutions()); the importer then scales it down to 1080p HEVC with a QUALITY target (constant quality = variable bitrate:
simple scenes get small files, busy ones get more bits) instead of a fixed bitrate. Hardware (VAAPI, e.g. Intel QuickSync through /dev/dri) is used
when it works -- proven once by a one-second test encode -- else libx265 on the CPU.

Not done here: choosing when to transcode (file_importer._maybe_reencode()), verification of the result against the provider's fingerprints (a
re-encoded file has other hashes; the importer marks such scenes "reencoded" and finalizes them without fingerprint verification).
"""
import json
import logging
import os
import re
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

RENDER_DEVICE = "/dev/dri/renderD128"
_encode_lock = threading.Lock()  # one encode at a time: it uses every core (or the whole GPU)
_hw_state = {"checked": False, "ok": False, "reason": ""}


def probe(path, timeout=60):
    """{width, height, duration, vcodec, acodec, hdr, color} of a media file, or None when ffprobe cannot read it."""
    try:
        out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", path],
                             capture_output=True, text=True, timeout=timeout)
        data = json.loads(out.stdout)
    except Exception:
        logger.exception("transcode.probe: ffprobe failed for %s", path)
        return None
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        return None
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    try:
        duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    except ValueError:
        duration = 0.0
    transfer = video.get("color_transfer") or ""
    return {"width": video.get("width"), "height": video.get("height"), "duration": duration, "vcodec": video.get("codec_name"),
            "acodec": audio.get("codec_name") if audio else None,
            "hdr": transfer in ("smpte2084", "arib-std-b67"),
            "color": {"color_primaries": video.get("color_primaries"), "color_trc": transfer, "colorspace": video.get("color_space")}}


def target_size(width, height, max_w=1920, max_h=1080):
    """Largest size that fits max_w x max_h with the source's aspect ratio, even numbers (the encoders need them)."""
    scale = min(max_w / width, max_h / height, 1.0)
    w, h = int(width * scale) // 2 * 2, int(height * scale) // 2 * 2
    return max(w, 2), max(h, 2)


def hw_available():
    """True when a one-second VAAPI test encode works here (cached for the life of the process). Never raises."""
    if _hw_state["checked"]:
        return _hw_state["ok"]
    _hw_state["checked"] = True
    if not os.path.exists(RENDER_DEVICE):
        _hw_state["reason"] = f"{RENDER_DEVICE} not present"
        return False
    try:
        cmd = ["ffmpeg", "-v", "error", "-vaapi_device", RENDER_DEVICE, "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=1",
               "-vf", "format=nv12,hwupload", "-c:v", "hevc_vaapi", "-qp", "30", "-f", "null", "-"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        _hw_state["ok"] = res.returncode == 0
        _hw_state["reason"] = (res.stderr or "").strip()[-200:] if res.returncode else ""
    except Exception as e:
        _hw_state["reason"] = f"{type(e).__name__}: {e}"
    logger.info("transcode: hardware encoding %s%s", "available" if _hw_state["ok"] else "not available", f" ({_hw_state['reason']})" if _hw_state["reason"] else "")
    return _hw_state["ok"]


def output_extension(info):
    """mp4 when the audio can stay as it is in an mp4, else mkv (audio is copied, never re-encoded)."""
    return ".mp4" if (info.get("acodec") in (None, "aac", "ac3", "eac3", "mp3", "alac")) else ".mkv"


def build_command(src, dst, info, quality, use_hw):
    """The ffmpeg command line (a list). `quality`: lower = better; x265 CRF as given, VAAPI constant QP = quality + 1."""
    tw, th = target_size(info["width"], info["height"])
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin", "-progress", "pipe:1", "-nostats"]
    hdr = info.get("hdr")
    if use_hw and not hdr:
        cmd += ["-vaapi_device", RENDER_DEVICE, "-i", src, "-map", "0:v:0", "-map", "0:a?",
                "-vf", f"format=nv12,hwupload,scale_vaapi=w={tw}:h={th}", "-c:v", "hevc_vaapi", "-rc_mode", "CQP", "-qp", str(int(quality) + 1)]
    else:
        cmd += ["-i", src, "-map", "0:v:0", "-map", "0:a?", "-vf", f"scale={tw}:{th}:flags=lanczos", "-c:v", "libx265", "-preset", "medium", "-crf", str(int(quality))]
        if hdr:  # keep HDR: 10-bit and the colour tags (rare for this kind of release)
            cmd += ["-pix_fmt", "yuv420p10le"]
            for flag, key in (("-color_primaries", "color_primaries"), ("-color_trc", "color_trc"), ("-colorspace", "colorspace")):
                if (info.get("color") or {}).get(key):
                    cmd += [flag, info["color"][key]]
        else:
            cmd += ["-pix_fmt", "yuv420p"]
    cmd += ["-tag:v", "hvc1", "-c:a", "copy", "-sn", "-map_metadata", "0", dst]
    return cmd


def verify_output(dst, source_info):
    """None when the encoded file looks right (1080p class, the same length), else the reason."""
    out = probe(dst)
    if not out:
        return "the encoded file cannot be read"
    if (out["height"] or 0) > 1088 or (out["width"] or 0) > 1928:
        return f"the encoded file is still {out['width']}x{out['height']}"
    if source_info["duration"] and abs(out["duration"] - source_info["duration"]) > max(2.0, source_info["duration"] * 0.01):
        return f"the encoded file is {out['duration']:.0f} s long, the original {source_info['duration']:.0f} s"
    return None


def transcode(src, dst, quality=24, hw_mode="auto", progress=None, timeout_hours=12, info=None):
    """Encodes `src` to `dst`. `progress(percent, speed_x, seconds_left)` is called while it runs. Returns
    {"ok": True, "hw": bool, "seconds": n, "info": src_info, "out": out_info} or {"ok": False, "error": text}. Never raises; removes a half-written dst."""
    info = info or probe(src)
    if not info or not info.get("width") or not info.get("height"):
        return {"ok": False, "error": "the downloaded file cannot be read by ffprobe"}
    use_hw = hw_mode != "off" and hw_available()
    for attempt_hw in ([True, False] if use_hw else [False]):
        started = time.time()
        with _encode_lock:
            res = _run(build_command(src, dst, info, quality, attempt_hw), info, progress, timeout_hours)
        if res is None:
            problem = verify_output(dst, info)
            if problem is None:
                return {"ok": True, "hw": attempt_hw, "seconds": int(time.time() - started), "info": info, "out": probe(dst)}
            res = problem
        _remove(dst)
        logger.warning("transcode: %s encode of %s failed: %s", "hardware" if attempt_hw else "software", os.path.basename(src), res)
        if not attempt_hw:
            return {"ok": False, "error": res}
    return {"ok": False, "error": "encoding failed"}  # not reached


def _remove(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        logger.exception("transcode: could not remove %s", path)


def _run(cmd, info, progress, timeout_hours):
    """Runs ffmpeg; None on success, else an error text."""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
                                preexec_fn=lambda: os.nice(10))  # the rest of the machine comes first
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    started = time.time()
    deadline = started + timeout_hours * 3600
    last_pct = -1
    err_lines = []
    t = threading.Thread(target=lambda: err_lines.extend(proc.stderr.readlines()[-20:]), daemon=True)
    t.start()
    for line in proc.stdout:
        if time.time() > deadline:
            proc.kill()
            return f"stopped after {timeout_hours} h"
        m = re.match(r"out_time_(?:us|ms)=(\d+)", line)
        if m and info["duration"] and progress:
            done = int(m.group(1)) / 1_000_000
            pct = min(int(done * 100 / info["duration"]), 99)
            if pct != last_pct:
                last_pct = pct
                elapsed = max(time.time() - started, 0.001)
                speed = done / elapsed
                progress(pct, round(speed, 2), int((info["duration"] - done) / speed) if speed > 0 else None)
    proc.wait()
    t.join(timeout=5)
    if proc.returncode != 0:
        return (err_lines[-1].strip() if err_lines else f"ffmpeg exited with {proc.returncode}")[:300]
    return None
