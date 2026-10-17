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
_hw_state = {}  # per codec ("h265"/"h264"): {"checked", "ok", "reason", "low_power"}
VAAPI_ENCODER = {"h265": "hevc_vaapi", "h264": "h264_vaapi"}
PRESETS = ("medium", "fast", "veryfast")


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


def _error_tail(stderr):
    """The last meaningful line of ffmpeg's stderr, shortened."""
    lines = [l.strip() for l in (stderr or "").splitlines() if l.strip()]
    return (lines[-1] if lines else "no output")[-120:]


def hw_available(codec="h265"):
    """True when a one-second VAAPI test encode works here (cached per codec for the life of the process). Never raises.
    Tries the normal encoder first, then `-low_power 1` (the fixed-function encoder, the only one on some Intel chips); the one that works is
    kept in _hw_state[codec]["low_power"] for build_command()."""
    st = _hw_state.setdefault(codec, {"checked": False, "ok": False, "reason": "", "low_power": False})
    if st["checked"]:
        return st["ok"]
    st["checked"] = True
    if not os.path.exists(RENDER_DEVICE):
        st["reason"] = f"{RENDER_DEVICE} not present"
        return False
    problems = []
    for low_power in (False, True):
        try:
            cmd = ["ffmpeg", "-v", "error", "-vaapi_device", RENDER_DEVICE, "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=1",
                   "-vf", "format=nv12,hwupload", "-c:v", VAAPI_ENCODER[codec]] + (["-low_power", "1"] if low_power else []) + ["-qp", "30", "-f", "null", "-"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except Exception as e:
            problems.append(f"{'low_power' if low_power else 'default'}: {type(e).__name__}: {e}")
            continue
        if res.returncode == 0:
            st["ok"], st["low_power"], st["reason"] = True, low_power, ""
            break
        problems.append(f"{'low_power' if low_power else 'default'}: {_error_tail(res.stderr)}")
    else:
        st["reason"] = "; ".join(problems)
    logger.info("transcode: %s hardware encoding %s%s", codec, "available" if st["ok"] else "not available",
                " (low_power)" if st["low_power"] else f" ({st['reason']})" if st["reason"] else "")
    return st["ok"]


def method_label(hw_mode="auto", codec="h265"):
    """'hardware' or 'CPU (<why>)' -- what transcode() will try first, for the progress line and the history."""
    if hw_mode == "off":
        return "CPU (hardware encoding switched off)"
    if hw_available(codec):
        return "hardware"
    return f"CPU (hardware not available: {_hw_state[codec]['reason'] or 'unknown'})"


def output_extension(info):
    """mp4 when the audio can stay as it is in an mp4, else mkv (audio is copied, never re-encoded)."""
    return ".mp4" if (info.get("acodec") in (None, "aac", "ac3", "eac3", "mp3", "alac")) else ".mkv"


def build_command(src, dst, info, quality, use_hw, codec="h265", preset="fast"):
    """The ffmpeg command line (a list). `quality`: lower = better; x265 CRF as given (H.265), x264 CRF = quality - 4 (H.264 needs about that
    much lower for the same look), VAAPI constant QP = CRF + 1. An HDR source is always kept as 10-bit H.265 on the CPU."""
    if info.get("hdr"):
        codec, use_hw = "h265", False
    if preset not in PRESETS:
        preset = "fast"
    tw, th = target_size(info["width"], info["height"])
    crf = int(quality) if codec == "h265" else max(int(quality) - 4, 10)
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin", "-progress", "pipe:1", "-nostats"]
    if use_hw:
        low_power = _hw_state.get(codec, {}).get("low_power")
        cmd += ["-vaapi_device", RENDER_DEVICE, "-i", src, "-map", "0:v:0", "-map", "0:a?",
                "-vf", f"format=nv12,hwupload,scale_vaapi=w={tw}:h={th}", "-c:v", VAAPI_ENCODER[codec]] \
               + (["-low_power", "1"] if low_power else []) + ["-rc_mode", "CQP", "-qp", str(crf + 1)]
    else:
        cmd += ["-i", src, "-map", "0:v:0", "-map", "0:a?", "-vf", f"scale={tw}:{th}:flags=lanczos",
                "-c:v", "libx265" if codec == "h265" else "libx264", "-preset", preset, "-crf", str(crf)]
        if info.get("hdr"):  # keep HDR: 10-bit and the colour tags (rare for this kind of release)
            cmd += ["-pix_fmt", "yuv420p10le"]
            for flag, key in (("-color_primaries", "color_primaries"), ("-color_trc", "color_trc"), ("-colorspace", "colorspace")):
                if (info.get("color") or {}).get(key):
                    cmd += [flag, info["color"][key]]
        else:
            cmd += ["-pix_fmt", "yuv420p"]
    if codec == "h265":
        cmd += ["-tag:v", "hvc1"]
    cmd += ["-c:a", "copy", "-sn", "-map_metadata", "0", dst]
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


def transcode(src, dst, quality=24, hw_mode="auto", progress=None, timeout_hours=12, info=None, codec="h265", preset="fast"):
    """Encodes `src` to `dst`. `progress(percent, speed_x, seconds_left)` is called while it runs. Returns
    {"ok": True, "hw": bool, "seconds": n, "info": src_info, "out": out_info} or {"ok": False, "error": text}. Never raises; removes a half-written dst."""
    info = info or probe(src)
    if not info or not info.get("width") or not info.get("height"):
        return {"ok": False, "error": "the downloaded file cannot be read by ffprobe"}
    if info.get("hdr"):
        codec = "h265"
    use_hw = hw_mode != "off" and not info.get("hdr") and hw_available(codec)
    for attempt_hw in ([True, False] if use_hw else [False]):
        started = time.time()
        with _encode_lock:
            res = _run(build_command(src, dst, info, quality, attempt_hw, codec, preset), info, progress, timeout_hours)
        if res is None:
            problem = verify_output(dst, info)
            if problem is None:
                return {"ok": True, "hw": attempt_hw, "codec": codec, "seconds": int(time.time() - started), "info": info, "out": probe(dst)}
            res = problem
        _remove(dst)
        if res == "stopped":
            return {"ok": False, "error": "stopped"}  # the caller cancelled: no second try
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


class StopEncode(Exception):
    """Raised by a progress callback to stop the running encode (an encode worker that lost its job)."""


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
                try:
                    progress(pct, round(speed, 2), int((info["duration"] - done) / speed) if speed > 0 else None)
                except StopEncode:
                    proc.kill()
                    proc.wait()
                    return "stopped"
    proc.wait()
    t.join(timeout=5)
    if proc.returncode != 0:
        return (err_lines[-1].strip() if err_lines else f"ffmpeg exited with {proc.returncode}")[:300]
    return None
