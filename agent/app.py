import asyncio
import _thread
import json
import logging
import re
import socket
import os
import secrets
import ssl
import threading
import time
import sys
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Dict, Optional

import mss
import numpy as np
import pyautogui
import pyperclip
from aiohttp import ClientSession, ClientTimeout, web
from selkies_gst_presets import choose_platform_preset

try:
    import pydirectinput
    HAS_PYDIRECTINPUT = True
except Exception:
    pydirectinput = None
    HAS_PYDIRECTINPUT = False

try:
    import cv2
    HAS_CV2 = True
except Exception:
    HAS_CV2 = False

try:
    import dxcam
    HAS_DXCAM = True
except Exception:
    HAS_DXCAM = False

try:
    from PIL import Image, ImageGrab
    HAS_IMAGEGRAB = True
    HAS_PIL_IMAGE = True
except Exception:
    HAS_IMAGEGRAB = False
    HAS_PIL_IMAGE = False

try:
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
    HAS_RICH = True
except Exception:
    HAS_RICH = False

try:
    import gi
except Exception:
    gi = None


def _configure_gst_python_paths() -> None:
    if os.name != "nt":
        return

    candidates = [
        Path(r"C:\gstreamer\1.0\msvc_x86_64"),
        Path(r"C:\Program Files\gstreamer\1.0\msvc_x86_64"),
        Path(r"C:\Program Files\GStreamer\1.0\msvc_x86_64"),
        Path.home() / "AppData" / "Local" / "Programs" / "gstreamer" / "1.0" / "msvc_x86_64",
    ]
    for root in candidates:
        bin_dir = root / "bin"
        site_dir = root / "lib" / "site-packages"
        typelib_dir = root / "lib" / "girepository-1.0"
        if (bin_dir / "gst-launch-1.0.exe").exists():
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            os.environ["PYTHONPATH"] = str(site_dir) + os.pathsep + os.environ.get("PYTHONPATH", "")
            os.environ["GI_TYPELIB_PATH"] = str(typelib_dir)
            if str(site_dir) not in sys.path:
                sys.path.insert(0, str(site_dir))
            return


_configure_gst_python_paths()

if gi is None:
    try:
        import gi  # type: ignore
    except Exception:
        gi = None

if gi is not None:
    gi.require_version("Gst", "1.0")
    gi.require_version("GstWebRTC", "1.0")
    gi.require_version("GstSdp", "1.0")
    from gi.repository import GLib, Gst, GstSdp, GstWebRTC
    Gst.init(None)


logger = logging.getLogger("warpdesk-agent")
logging.basicConfig(level=logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists() or not env_path.is_file():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            # Do not override environment values that are already defined externally.
            os.environ.setdefault(key, value)
    except Exception:
        logger.warning("Failed to read env file: %s", env_path)


def _load_local_env_files() -> None:
    repo_root = Path.cwd()
    agent_dir = Path(__file__).resolve().parent
    candidates = [
        repo_root / ".env",
        repo_root / ".env.local",
        agent_dir / ".env",
        agent_dir / ".env.local",
    ]
    for env_path in candidates:
        _load_env_file(env_path)


_load_local_env_files()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


PORT = int(os.getenv("WARPDESK_PORT", "8443"))
USERNAME = os.getenv("WARPDESK_USER", "admin")
PASSWORD = os.getenv("WARPDESK_PASSWORD", "warpdesk")
TARGET_FPS_DEFAULT = int(os.getenv("WARPDESK_FPS", "60"))
SCALE_DEFAULT = int(os.getenv("WARPDESK_SCALE", "100"))
MAX_FPS = int(os.getenv("WARPDESK_MAX_FPS", "120"))
MAX_SESSIONS = int(os.getenv("WARPDESK_MAX_SESSIONS", "2"))
SESSION_TTL_SECONDS = int(os.getenv("WARPDESK_SESSION_TTL_SECONDS", str(12 * 3600)))
VIDEO_MAX_BITRATE_BPS = int(os.getenv("WARPDESK_VIDEO_MAX_BITRATE_BPS", "20000000"))
ENABLE_TUI = os.getenv("WARPDESK_TUI", "1") == "1"
CODEC_PREFERENCE = os.getenv("WARPDESK_CODEC", "vp8").strip().lower()
ALLOW_META_KEY = os.getenv("WARPDESK_ALLOW_META_KEY", "0") == "1"
INPUT_STUCK_RELEASE_SECONDS = float(os.getenv("WARPDESK_INPUT_STUCK_RELEASE_SECONDS", "2.0"))
INPUT_BACKEND = os.getenv("WARPDESK_INPUT_BACKEND", "auto").strip().lower()
AUDIO_SOURCE = os.getenv("WARPDESK_AUDIO_SOURCE", "system").strip().lower()
ALLOW_MIC_FALLBACK = os.getenv("WARPDESK_ALLOW_MIC_FALLBACK", "0") == "1"
SDP_MIN_BITRATE_KBPS = int(os.getenv("WARPDESK_SDP_MIN_BITRATE_KBPS", "1500"))
SDP_START_BITRATE_KBPS = int(os.getenv("WARPDESK_SDP_START_BITRATE_KBPS", "8000"))
FORCE_RICH_TUI = os.getenv("WARPDESK_FORCE_RICH_TUI", "1") == "1"
FORCED_LAN_IP = os.getenv("WARPDESK_FORCED_LAN_IP", "192.168.0.198").strip()
ICE_STUN_SERVERS = [
    "stun:stun.l.google.com:19302",
    "stun:stun1.l.google.com:19302",
]
TURN_SERVER_URLS = [u.strip() for u in os.getenv("WARPDESK_TURN_URLS", "").split(",") if u.strip()]
TURN_USERNAME = os.getenv("WARPDESK_TURN_USERNAME", "").strip()
TURN_CREDENTIAL = os.getenv("WARPDESK_TURN_CREDENTIAL", "").strip()
ICE_SERVERS_JSON = os.getenv("WARPDESK_ICE_SERVERS_JSON", "").strip()
GST_STUN_SERVER = os.getenv("WARPDESK_GST_STUN_SERVER", "stun://stun.l.google.com:19302").strip()
GST_TURN_SERVER = os.getenv("WARPDESK_GST_TURN_SERVER", "").strip()

# Cloudflare TURN short-lived credentials.
# Keep these in local env files or host environment variables only.
CLOUDFLARE_TURN_ENABLED = _env_bool("CLOUDFLARE_TURN", _env_bool("WARPDESK_CLOUDFLARE_TURN", True))
CLOUDFLARE_TURN_TOKEN_ID = os.getenv("WARPDESK_CF_TURN_TOKEN_ID", "").strip()
CLOUDFLARE_TURN_API_TOKEN = os.getenv("WARPDESK_CF_TURN_API_TOKEN", "").strip()
CLOUDFLARE_TURN_TTL_SECONDS = int(os.getenv("WARPDESK_CF_TURN_TTL_SECONDS", "3600"))
CLOUDFLARE_TURN_API_BASE = os.getenv("WARPDESK_CF_TURN_API_BASE", "https://rtc.live.cloudflare.com/v1").strip().rstrip("/")
CLOUDFLARE_TURN_TIMEOUT_SECONDS = float(os.getenv("WARPDESK_CF_TURN_TIMEOUT_SECONDS", "8"))


def build_ice_servers() -> list[dict]:
    if ICE_SERVERS_JSON:
        try:
            parsed = json.loads(ICE_SERVERS_JSON)
            if isinstance(parsed, list):
                valid = [item for item in parsed if isinstance(item, dict) and item.get("urls")]
                if valid:
                    return valid
        except Exception:
            pass

    ice_servers: list[dict] = []
    if ICE_STUN_SERVERS:
        ice_servers.append({"urls": ICE_STUN_SERVERS})

    if TURN_SERVER_URLS and TURN_USERNAME and TURN_CREDENTIAL:
        ice_servers.append({
            "urls": TURN_SERVER_URLS,
            "username": TURN_USERNAME,
            "credential": TURN_CREDENTIAL,
        })
    return ice_servers

os.environ.setdefault("GST_SCHEDULING", "sync")
os.environ.setdefault("GST_DEBUG", "0")

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0
if HAS_PYDIRECTINPUT:
    try:
        pydirectinput.FAILSAFE = False
        pydirectinput.PAUSE = 0
    except Exception:
        pass


@dataclass
class Session:
    username: str
    created_at: float


@dataclass
class LoginEntry:
    ip: str
    username: str
    time: str


class RuntimeConfig:
    def __init__(self) -> None:
        self.target_fps = TARGET_FPS_DEFAULT
        self.scale = SCALE_DEFAULT


class RuntimeStats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.capture_frames = 0
        self.sent_frames = 0
        self.input_events = 0
        self.input_drops = 0
        self.audio_drops = 0
        self.capture_failures = 0
        self.capture_mode = "mss"
        self.last_rgb_max = 0
        self.active_peers = 0
        self.last_client_ip = "unknown"
        self.connected_since: Optional[float] = None

    def add_capture_frame(self, rgb_max: int, mode: str):
        with self._lock:
            self.capture_frames += 1
            self.last_rgb_max = int(rgb_max)
            self.capture_mode = mode

    def add_sent_frame(self):
        with self._lock:
            self.sent_frames += 1

    def add_input_event(self):
        with self._lock:
            self.input_events += 1

    def add_input_drop(self):
        with self._lock:
            self.input_drops += 1

    def add_audio_drop(self):
        with self._lock:
            self.audio_drops += 1

    def add_capture_failure(self):
        with self._lock:
            self.capture_failures += 1

    def set_active_peers(self, value: int):
        with self._lock:
            self.active_peers = value

    def set_last_client_ip(self, client_ip: str):
        with self._lock:
            self.last_client_ip = client_ip or "unknown"

    def mark_connected_now(self):
        with self._lock:
            self.connected_since = time.time()

    def clear_connected_since_if_no_peers(self):
        with self._lock:
            if self.active_peers <= 0:
                self.connected_since = None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "capture_frames": self.capture_frames,
                "sent_frames": self.sent_frames,
                "input_events": self.input_events,
                "input_drops": self.input_drops,
                "audio_drops": self.audio_drops,
                "capture_failures": self.capture_failures,
                "capture_mode": self.capture_mode,
                "last_rgb_max": self.last_rgb_max,
                "active_peers": self.active_peers,
                "last_client_ip": self.last_client_ip,
                "connected_since": self.connected_since,
            }


class ScreenCaptureWorker:
    def __init__(self, cfg: RuntimeConfig, stats: RuntimeStats):
        self.cfg = cfg
        self.stats = stats
        self._latest_lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_dims = (1280, 720)
        self._bbox: Optional[tuple[int, int, int, int]] = None
        self._last_error: Optional[str] = None
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _capture_fallback(self) -> Optional[np.ndarray]:
        # Fallback path keeps stream alive if mss fails temporarily.
        try:
            if HAS_IMAGEGRAB:
                if self._bbox is not None:
                    rgb = np.array(ImageGrab.grab(bbox=self._bbox))
                else:
                    rgb = np.array(ImageGrab.grab(all_screens=True))
            else:
                shot = pyautogui.screenshot()
                rgb = np.array(shot)
            # pyautogui returns RGB; convert to BGRA for the encoder path.
            bgr = rgb[:, :, ::-1]
            alpha = np.full((bgr.shape[0], bgr.shape[1], 1), 255, dtype=np.uint8)
            return np.concatenate([bgr, alpha], axis=2)
        except Exception as e:
            self._last_error = str(e)
            self.stats.add_capture_failure()
            return None

    def _run(self):
        monitor_index = int(os.getenv("WARPDESK_MONITOR_INDEX", "1"))
        dx_cam = None
        dx_cam_fps = 0

        if HAS_DXCAM:
            try:
                output_idx = max(0, monitor_index - 1)
                dx_cam = dxcam.create(output_idx=output_idx)
                dx_cam_fps = max(30, int(self.cfg.target_fps))
                dx_cam.start(target_fps=dx_cam_fps, video_mode=True)
                logger.info("Using dxcam capture backend (output_idx=%s)", output_idx)
            except Exception:
                dx_cam = None

        try:
            sct = mss.mss()
            if 0 <= monitor_index < len(sct.monitors):
                monitor = sct.monitors[monitor_index]
            elif len(sct.monitors) > 1:
                monitor = sct.monitors[1]
            else:
                monitor = sct.monitors[0]
            self._bbox = (
                int(monitor.get("left", 0)),
                int(monitor.get("top", 0)),
                int(monitor.get("left", 0)) + int(monitor.get("width", 1280)),
                int(monitor.get("top", 0)) + int(monitor.get("height", 720)),
            )
        except Exception:
            sct = None
            monitor = None
            logger.exception("mss initialization failed; fallback capture will be used")

        next_at = time.perf_counter()
        black_count = 0
        last_warn_at = 0.0
        while not self._stopped.is_set():
            fps = max(1, int(self.cfg.target_fps))
            frame_period = 1.0 / fps

            if dx_cam is not None:
                desired_dx_fps = max(30, fps)
                if desired_dx_fps != dx_cam_fps:
                    try:
                        dx_cam.stop()
                        dx_cam.start(target_fps=desired_dx_fps, video_mode=True)
                        dx_cam_fps = desired_dx_fps
                    except Exception:
                        pass

            now = time.perf_counter()
            if next_at > now:
                time.sleep(next_at - now)
            next_at = max(next_at + frame_period, time.perf_counter())

            if sct is None or monitor is None:
                fb = self._capture_fallback()
                with self._latest_lock:
                    if fb is not None:
                        self._latest_frame = fb
                        self._latest_dims = (fb.shape[1], fb.shape[0])
                    elif self._latest_frame is None:
                        self._latest_frame = np.zeros((720, 1280, 4), dtype=np.uint8)
                        self._latest_dims = (1280, 720)
                continue

            frame_bgra = None

            if dx_cam is not None:
                try:
                    rgb = dx_cam.get_latest_frame()
                    if rgb is not None and rgb.ndim == 3 and rgb.shape[2] == 3:
                        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
                        alpha = np.full((bgr.shape[0], bgr.shape[1], 1), 255, dtype=np.uint8)
                        frame_bgra = np.concatenate([bgr, alpha], axis=2)
                except Exception:
                    frame_bgra = None

            try:
                if frame_bgra is None:
                    shot = sct.grab(monitor)
                    frame_bgra = np.frombuffer(shot.bgra, dtype=np.uint8).reshape((shot.height, shot.width, 4))
                    source_mode = "mss"
                else:
                    source_mode = "dxcam"

                # Some systems return all-black DXGI frames; switch to screenshot fallback.
                if int(frame_bgra[:, :, :3].max()) <= 2:
                    black_count += 1
                    if black_count >= 3:
                        fb = self._capture_fallback()
                        if fb is not None:
                            frame_bgra = fb
                else:
                    black_count = 0

                scale = max(10, min(100, int(self.cfg.scale)))
                if scale < 100 and HAS_CV2:
                    src_h, src_w = frame_bgra.shape[:2]
                    w = max(2, (src_w * scale // 100) & ~1)
                    h = max(2, (src_h * scale // 100) & ~1)
                    frame_bgra = cv2.resize(frame_bgra, (w, h), interpolation=cv2.INTER_AREA)
                elif scale < 100 and HAS_PIL_IMAGE:
                    src_h, src_w = frame_bgra.shape[:2]
                    w = max(2, (src_w * scale // 100) & ~1)
                    h = max(2, (src_h * scale // 100) & ~1)
                    rgba = frame_bgra[:, :, [2, 1, 0, 3]]
                    pil_img = Image.fromarray(rgba, mode="RGBA").resize((w, h), Image.Resampling.BILINEAR)
                    rgba_resized = np.array(pil_img)
                    frame_bgra = rgba_resized[:, :, [2, 1, 0, 3]]

                with self._latest_lock:
                    self._latest_frame = frame_bgra
                    self._latest_dims = (frame_bgra.shape[1], frame_bgra.shape[0])
                    self._last_error = None
            except Exception:
                fb = self._capture_fallback()
                if fb is not None:
                    with self._latest_lock:
                        self._latest_frame = fb
                        self._latest_dims = (fb.shape[1], fb.shape[0])
                        self.stats.add_capture_frame(int(fb[:, :, :3].max()), "fallback")
                now_warn = time.time()
                if now_warn - last_warn_at > 5:
                    last_warn_at = now_warn
                    logger.warning("Screen capture failed; using fallback screenshot path")
                self.stats.add_capture_failure()
                continue

            with self._latest_lock:
                self.stats.add_capture_frame(int(frame_bgra[:, :, :3].max()), source_mode)

    def latest_frame(self) -> np.ndarray:
        with self._latest_lock:
            if self._latest_frame is None:
                w, h = self._latest_dims
                return np.zeros((h, w, 4), dtype=np.uint8)
            return self._latest_frame.copy()

    def stop(self):
        self._stopped.set()
        if self._thread.is_alive():
            self._thread.join(timeout=0.5)


def get_local_ip() -> str:
    if FORCED_LAN_IP and FORCED_LAN_IP not in {"127.0.0.1", "0.0.0.0"}:
        return FORCED_LAN_IP
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def _force_lan_ip_in_sdp(sdp: str, forced_ip: str) -> str:
    if not forced_ip:
        return sdp

    out: list[str] = []
    for raw in sdp.splitlines():
        line = raw
        if line.startswith("c=IN IP4 "):
            ip = line[len("c=IN IP4 "):].strip()
            if ip in {"127.0.0.1", "0.0.0.0"}:
                line = f"c=IN IP4 {forced_ip}"

        if line.startswith("a=candidate:"):
            parts = line.split()
            # Candidate address is at index 4 for UDP/TCP candidates.
            if len(parts) >= 6 and parts[4] in {"127.0.0.1", "0.0.0.0"}:
                parts[4] = forced_ip
                line = " ".join(parts)

        out.append(line)

    return "\r\n".join(out) + "\r\n"


def _force_media_sendonly_in_sdp(sdp: str) -> str:
    lines = sdp.splitlines()
    if not lines:
        return sdp

    out: list[str] = []
    current_media: Optional[str] = None

    for line in lines:
        if line.startswith("m="):
            try:
                current_media = line[2:].split(" ", 1)[0].strip().lower()
            except Exception:
                current_media = None
            out.append(line)
            continue

        if current_media in {"audio", "video"} and line in {
            "a=inactive",
            "a=sendrecv",
            "a=recvonly",
        }:
            out.append("a=sendonly")
            continue

        out.append(line)

    result: list[str] = []
    current_media = None
    has_direction = False

    for idx, line in enumerate(out):
        if line.startswith("m="):
            if current_media in {"audio", "video"} and not has_direction:
                result.append("a=sendonly")
            try:
                current_media = line[2:].split(" ", 1)[0].strip().lower()
            except Exception:
                current_media = None
            has_direction = False
            result.append(line)
            continue

        if current_media in {"audio", "video"} and line in {
            "a=inactive",
            "a=sendrecv",
            "a=recvonly",
            "a=sendonly",
        }:
            has_direction = True

        result.append(line)

    if current_media in {"audio", "video"} and not has_direction:
        result.append("a=sendonly")

    return "\r\n".join(result) + "\r\n"


def _append_codec_fmtp_params(sdp_lines: list[str], payload_type: str, add_params: dict[str, int]) -> list[str]:
    keyvals = [f"{k}={v}" for k, v in add_params.items()]
    if not keyvals:
        return sdp_lines

    fmtp_prefix = f"a=fmtp:{payload_type} "
    rtpmap_prefix = f"a=rtpmap:{payload_type} "
    fmtp_idx = None
    rtpmap_idx = None

    for idx, line in enumerate(sdp_lines):
        if line.startswith(rtpmap_prefix):
            rtpmap_idx = idx
        if line.startswith(fmtp_prefix):
            fmtp_idx = idx

    if fmtp_idx is not None:
        existing = sdp_lines[fmtp_idx][len(fmtp_prefix):].strip()
        params = [p.strip() for p in existing.split(";") if p.strip()]
        seen = {p.split("=", 1)[0].strip() for p in params if "=" in p}
        for kv in keyvals:
            k = kv.split("=", 1)[0]
            if k not in seen:
                params.append(kv)
        sdp_lines[fmtp_idx] = fmtp_prefix + ";".join(params)
        return sdp_lines

    if rtpmap_idx is not None:
        sdp_lines.insert(rtpmap_idx + 1, fmtp_prefix + ";".join(keyvals))

    return sdp_lines


def tune_answer_sdp(sdp: str, target_fps: int, max_bitrate_bps: int) -> str:
    lines = sdp.splitlines()
    if not lines:
        return sdp

    max_bitrate_kbps = max(1000, int(max_bitrate_bps // 1000))
    min_bitrate_kbps = max(0, int(SDP_MIN_BITRATE_KBPS))
    start_bitrate_kbps = max(min_bitrate_kbps, int(SDP_START_BITRATE_KBPS))

    out: list[str] = []
    in_video = False
    inserted_as = False

    for line in lines:
        if line.startswith("m=video "):
            in_video = True
            inserted_as = False
            out.append(line)
            continue
        if in_video and line.startswith("m="):
            in_video = False

        if in_video and (not inserted_as) and line.startswith("c="):
            out.append(line)
            out.append(f"b=AS:{max_bitrate_kbps}")
            inserted_as = True
            continue

        out.append(line)

    payload_types: list[tuple[str, str]] = []
    for line in out:
        if not line.startswith("a=rtpmap:"):
            continue
        try:
            rest = line[len("a=rtpmap:"):]
            pt, codec = rest.split(" ", 1)
            codec_name = codec.split("/", 1)[0].strip().lower()
            payload_types.append((pt.strip(), codec_name))
        except Exception:
            continue

    for pt, codec_name in payload_types:
        if codec_name not in {"vp8", "h264"}:
            continue
        params = {
            "x-google-min-bitrate": min_bitrate_kbps,
            "x-google-start-bitrate": start_bitrate_kbps,
            "x-google-max-bitrate": max_bitrate_kbps,
            "max-fr": max(30, int(target_fps)),
            "max-fs": 8160,
        }
        out = _append_codec_fmtp_params(out, pt, params)

    tuned = "\r\n".join(out) + "\r\n"
    tuned = _force_media_sendonly_in_sdp(tuned)
    return _force_lan_ip_in_sdp(tuned, FORCED_LAN_IP)


def keep_h264_only(sdp: str) -> str:
    lines = sdp.splitlines()
    if not lines:
        return sdp

    # Split SDP into session lines and per-media sections.
    session_lines: list[str] = []
    sections: list[list[str]] = []
    current_section: Optional[list[str]] = None
    for line in lines:
        if line.startswith("m="):
            current_section = [line]
            sections.append(current_section)
            continue
        if current_section is None:
            session_lines.append(line)
        else:
            current_section.append(line)

    def section_media_name(section: list[str]) -> str:
        try:
            return section[0][2:].split(" ", 1)[0].strip().lower()
        except Exception:
            return ""

    video_idx = next((i for i, sec in enumerate(sections) if section_media_name(sec) == "video"), None)
    if video_idx is None:
        return sdp

    video = sections[video_idx]
    h264_pts: list[str] = []
    profile_by_pt: dict[str, str] = {}
    packet_mode_by_pt: dict[str, str] = {}

    for line in video:
        m = re.match(r"a=rtpmap:(\d+)\s+H264/", line, re.IGNORECASE)
        if m:
            pt = m.group(1)
            if pt not in h264_pts:
                h264_pts.append(pt)
            continue

        m = re.match(r"a=fmtp:(\d+)\s+(.*)$", line, re.IGNORECASE)
        if m:
            pt = m.group(1)
            params = m.group(2)
            prof = re.search(r"profile-level-id=([0-9A-Fa-f]+)", params)
            if prof:
                profile_by_pt[pt] = prof.group(1).lower()
            pm = re.search(r"packetization-mode=(\d+)", params)
            if pm:
                packet_mode_by_pt[pt] = pm.group(1)

    if not h264_pts:
        raise RuntimeError("Offer SDP contains no H264 payloads")

    def _score(pt: str) -> tuple[int, int, int]:
        # Prefer packetization-mode=1 and baseline profile 42e01f.
        pm = 1 if packet_mode_by_pt.get(pt, "") == "1" else 0
        baseline = 1 if profile_by_pt.get(pt, "") == "42e01f" else 0
        has_profile = 1 if pt in profile_by_pt else 0
        return (pm, baseline, has_profile)

    preferred_pt = max(h264_pts, key=_score)

    ordered_h264_pts = [preferred_pt]

    filtered_video: list[str] = []
    video_mline = video[0].split()
    filtered_video.append(" ".join(video_mline[:3] + [preferred_pt]))

    selected_profile = profile_by_pt.get(preferred_pt, "")
    for line in video[1:]:
        m = re.match(r"a=(rtpmap|rtcp-fb|fmtp):(\d+)(?:\s+(.*))?$", line, re.IGNORECASE)
        if not m:
            filtered_video.append(line)
            continue

        attr = m.group(1).lower()
        pt = m.group(2)
        rest = m.group(3) or ""

        if pt != preferred_pt:
            continue

        if attr == "rtpmap":
            filtered_video.append(line)
            continue

        if attr == "fmtp":
            filtered_video.append(line)
            continue

        filtered_video.append(line)

    sections[video_idx] = filtered_video

    # Keep one compatible H264 payload to avoid webrtcbin codec ambiguity.
    video_mline = sections[video_idx][0].split()
    sections[video_idx][0] = " ".join(video_mline[:3] + ordered_h264_pts)

    out = session_lines[:]
    for sec in sections:
        out.extend(sec)
    logger.info(
        "SDP filter selected H264 PT=%s profile-level-id=%s packetization-mode=%s",
        preferred_pt,
        selected_profile or "(not provided)",
        packet_mode_by_pt.get(preferred_pt, "(not provided)"),
    )
    return "\r\n".join(out) + "\r\n"


def select_offer_payload_types(sdp: str) -> tuple[Optional[int], Optional[int]]:
    lines = sdp.splitlines()
    if not lines:
        return None, None

    video_pt: Optional[int] = None
    audio_pt: Optional[int] = None
    current_media: Optional[str] = None
    audio_opus_pts: set[str] = set()

    for line in lines:
        if line.startswith("m="):
            try:
                current_media = line[2:].split(" ", 1)[0].strip().lower()
            except Exception:
                current_media = None

            parts = line.split()
            if current_media == "video" and len(parts) > 3:
                try:
                    video_pt = int(parts[3])
                except Exception:
                    video_pt = None
            continue

        if re.match(r"a=rtpmap:(\d+)\s+opus/", line, re.IGNORECASE):
            m = re.match(r"a=rtpmap:(\d+)\s+opus/", line, re.IGNORECASE)
            if m:
                audio_opus_pts.add(m.group(1))

        if current_media == "audio" and line.startswith("m=audio"):
            parts = line.split()
            for p in parts[3:]:
                if p in audio_opus_pts:
                    try:
                        audio_pt = int(p)
                    except Exception:
                        audio_pt = None
                    break

    # Fallback: find first opus pt in any audio m-line ordering.
    if audio_pt is None:
        for line in lines:
            if line.startswith("m=audio"):
                parts = line.split()
                for p in parts[3:]:
                    if p in audio_opus_pts:
                        try:
                            audio_pt = int(p)
                        except Exception:
                            audio_pt = None
                        break
                if audio_pt is not None:
                    break

    return video_pt, audio_pt


class InputWorker:
    KEY_MAP = {
        "arrowup": "up",
        "arrowdown": "down",
        "arrowleft": "left",
        "arrowright": "right",
        " ": "space",
        "escape": "esc",
        "control": "ctrl",
        "alt": "alt",
        "shift": "shift",
        "tab": "tab",
        "delete": "delete",
        "insert": "insert",
        "home": "home",
        "end": "end",
        "meta": "win",
        "os": "win",
        "super": "win",
        "capslock": "capslock",
        "pagedown": "pagedown",
        "pageup": "pageup",
        "backspace": "backspace",
        "enter": "enter",
    }

    def __init__(self, stats: RuntimeStats):
        self.stats = stats
        self._q: Queue[dict] = Queue(maxsize=512)
        self._stopped = threading.Event()
        self._pressed_keys: set[str] = set()
        self._pressed_buttons: set[str] = set()
        self._last_input_ts = time.monotonic()
        self._state_lock = threading.Lock()
        use_direct = INPUT_BACKEND in {"auto", "direct", "pydirectinput"} and HAS_PYDIRECTINPUT
        if INPUT_BACKEND in {"pyautogui", "gui"}:
            use_direct = False
        self._use_direct_input = use_direct
        backend_name = "pydirectinput" if self._use_direct_input else "pyautogui"
        print(f"[INPUT] backend={backend_name}", flush=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _mouse_move(self, sx: int, sy: int) -> None:
        if self._use_direct_input:
            try:
                pydirectinput.moveTo(sx, sy)
                return
            except Exception:
                pass
        pyautogui.moveTo(sx, sy, duration=0, _pause=False)

    def _mouse_move_rel(self, dx: int, dy: int) -> None:
        # Clamp large deltas from accidental unlock/lock transitions.
        ddx = max(-200, min(200, int(dx)))
        ddy = max(-200, min(200, int(dy)))
        if ddx == 0 and ddy == 0:
            return
        if self._use_direct_input:
            try:
                pydirectinput.moveRel(ddx, ddy)
                return
            except Exception:
                pass
        pyautogui.moveRel(ddx, ddy, duration=0, _pause=False)

    def _mouse_down(self, button: str) -> None:
        if self._use_direct_input:
            try:
                pydirectinput.mouseDown(button=button)
                return
            except Exception:
                pass
        pyautogui.mouseDown(button=button)

    def _mouse_up(self, button: str) -> None:
        if self._use_direct_input:
            try:
                pydirectinput.mouseUp(button=button)
                return
            except Exception:
                pass
        pyautogui.mouseUp(button=button)

    def _key_down(self, key: str) -> None:
        if self._use_direct_input:
            try:
                pydirectinput.keyDown(key)
                return
            except Exception:
                pass
        pyautogui.keyDown(key)

    def _key_up(self, key: str) -> None:
        if self._use_direct_input:
            try:
                pydirectinput.keyUp(key)
                return
            except Exception:
                pass
        pyautogui.keyUp(key)

    def push(self, msg: dict):
        try:
            self._q.put_nowait(msg)
        except Full:
            self.stats.add_input_drop()
            try:
                _ = self._q.get_nowait()
            except Empty:
                pass
            try:
                self._q.put_nowait(msg)
            except Full:
                self.stats.add_input_drop()
                pass

    def _translate_key(self, key: str) -> str:
        k = key.lower()
        return self.KEY_MAP.get(k, k)

    def _normalize_button(self, button: str) -> str:
        b = str(button or "left").lower()
        if b in {"left", "middle", "right"}:
            return b
        return "left"

    def _safe_mouse_xy(self, x: float, y: float) -> tuple[int, int]:
        w, h = pyautogui.size()
        x = min(1.0, max(0.0, float(x)))
        y = min(1.0, max(0.0, float(y)))
        sx = max(0, min(w - 1, int(round(x * (w - 1)))))
        sy = max(0, min(h - 1, int(round(y * (h - 1)))))
        return sx, sy

    def _is_supported_key(self, key: str) -> bool:
        if not key:
            return False
        if len(key) == 1:
            return True
        allowed = {
            "up", "down", "left", "right", "space", "esc", "ctrl", "alt", "shift",
            "tab", "backspace", "enter", "delete", "insert", "home", "end",
            "pageup", "pagedown", "capslock", "win",
            "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
        }
        return key in allowed

    def _release_all_input(self) -> None:
        with self._state_lock:
            keys = list(self._pressed_keys)
            buttons = list(self._pressed_buttons)
            self._pressed_keys.clear()
            self._pressed_buttons.clear()

        for key in keys:
            try:
                self._key_up(key)
            except Exception:
                pass
        for button in buttons:
            try:
                self._mouse_up(button)
            except Exception:
                pass

    def reset(self) -> None:
        self._release_all_input()

    def _run(self):
        while not self._stopped.is_set():
            if time.monotonic() - self._last_input_ts > INPUT_STUCK_RELEASE_SECONDS:
                self._release_all_input()
                self._last_input_ts = time.monotonic()
            try:
                msg = self._q.get(timeout=0.05)
            except Empty:
                continue

            t = msg.get("type")
            try:
                self._last_input_ts = time.monotonic()
                self.stats.add_input_event()
                if t == "mousemove":
                    sx, sy = self._safe_mouse_xy(float(msg.get("x", 0.5)), float(msg.get("y", 0.5)))
                    self._mouse_move(sx, sy)
                elif t == "mousemove_rel":
                    dx = int(msg.get("dx", 0))
                    dy = int(msg.get("dy", 0))
                    self._mouse_move_rel(dx, dy)
                elif t == "mousedown":
                    btn = self._normalize_button(str(msg.get("button", "left")))
                    with self._state_lock:
                        self._pressed_buttons.add(btn)
                    self._mouse_down(btn)
                elif t == "mouseup":
                    btn = self._normalize_button(str(msg.get("button", "left")))
                    with self._state_lock:
                        self._pressed_buttons.discard(btn)
                    self._mouse_up(btn)
                elif t == "mousescroll":
                    dy = int(msg.get("dy", 0))
                    dx = int(msg.get("dx", 0))
                    if dy != 0:
                        pyautogui.scroll(-120 * dy)
                    if dx != 0:
                        try:
                            pyautogui.hscroll(120 * dx)
                        except Exception:
                            pass
                elif t == "keydown":
                    key = self._translate_key(str(msg.get("key", "")))
                    if key == "win" and not ALLOW_META_KEY:
                        continue
                    if self._is_supported_key(key):
                        with self._state_lock:
                            if key in self._pressed_keys:
                                continue
                            self._pressed_keys.add(key)
                        self._key_down(key)
                elif t == "keyup":
                    key = self._translate_key(str(msg.get("key", "")))
                    if key == "win" and not ALLOW_META_KEY:
                        continue
                    if self._is_supported_key(key):
                        with self._state_lock:
                            self._pressed_keys.discard(key)
                        self._key_up(key)
                elif t == "input_reset":
                    self._release_all_input()
            except Exception:
                pass

    def stop(self):
        self._stopped.set()
        self._release_all_input()
        if self._thread.is_alive():
            self._thread.join(timeout=0.5)


class GstPeer:
    def __init__(self, agent: "WarpDeskPyAgent", peer_id: str, candidate_sender=None):
        if gi is None:
            raise RuntimeError("Python GI/GStreamer bindings unavailable")
        self.agent = agent
        self.peer_id = peer_id
        self.loop = asyncio.get_running_loop()
        self.pipeline: Optional[Gst.Pipeline] = None
        self.webrtc = None
        self.control_channel = None
        self.input_channel = None
        self._answer_ready: Optional[asyncio.Future[str]] = None
        self._candidate_sender = candidate_sender
        self._video_probe_count = 0
        self._audio_probe_count = 0
        self._bus_thread: Optional[threading.Thread] = None
        self._bus_stop = threading.Event()
        self.connection_state = "new"

    def _pipeline_string(self) -> str:
        available_elements = set()
        try:
            registry = Gst.Registry.get()
            if registry is not None:
                for feat in registry.get_feature_list(Gst.ElementFactory):
                    try:
                        name = feat.get_name()
                    except Exception:
                        name = None
                    if name:
                        available_elements.add(str(name).strip().lower())
        except Exception:
            available_elements = set()

        preset = choose_platform_preset(
            self.agent.cfg.target_fps,
            self.agent.cfg.scale,
            platform_name=sys.platform,
            available_elements=available_elements,
        )
        self.agent.add_runtime_log(f"[GST] selected preset={preset.name}")
        video_branch = (
            f"{preset.video} ! "
            "application/x-rtp,media=video,encoding-name=H264 ! webrtc. "
        )
        audio_branch = (
            f"{preset.audio} ! "
            "application/x-rtp,media=audio,encoding-name=OPUS ! webrtc. "
        )
        return f"webrtcbin name=webrtc bundle-policy=max-bundle {video_branch} {audio_branch}"

    def start(self) -> None:
        desc = self._pipeline_string()
        print(f"[GST PIPELINE] {desc}", flush=True)
        pipeline = Gst.parse_launch(desc)
        if pipeline is None:
            raise RuntimeError("Failed to create GStreamer pipeline")
        self.pipeline = pipeline
        self.webrtc = pipeline.get_by_name("webrtc")
        if self.webrtc is None:
            raise RuntimeError("Failed to find webrtcbin element")

        # Configure optional STUN/TURN for host-side ICE gathering.
        try:
            if GST_STUN_SERVER:
                self.webrtc.set_property("stun-server", GST_STUN_SERVER)
            if GST_TURN_SERVER:
                self.webrtc.set_property("turn-server", GST_TURN_SERVER)
        except Exception:
            pass

        self.webrtc.connect("on-data-channel", self._on_data_channel)
        self.webrtc.connect("on-ice-candidate", self._on_ice_candidate)
        self.webrtc.connect("notify::connection-state", self._on_connection_state)

        # Ensure send transceivers exist before answer creation.
        try:
            v_caps = Gst.Caps.from_string(
                "application/x-rtp,media=video,encoding-name=H264,clock-rate=90000"
            )
            a_caps = Gst.Caps.from_string(
                "application/x-rtp,media=audio,encoding-name=OPUS,clock-rate=48000,encoding-params=2"
            )
            self.agent.add_runtime_log("[PC] adding video track...")
            self.webrtc.emit(
                "add-transceiver",
                GstWebRTC.WebRTCRTPTransceiverDirection.SENDONLY,
                v_caps,
            )
            self.agent.add_runtime_log("[PC] added video track")
            self.agent.add_runtime_log("[PC] adding audio track...")
            self.webrtc.emit(
                "add-transceiver",
                GstWebRTC.WebRTCRTPTransceiverDirection.SENDONLY,
                a_caps,
            )
            self.agent.add_runtime_log("[PC] added audio track")
        except Exception:
            pass

        self._attach_bus_watch()
        self._attach_media_flow_probes()
        self.pipeline.set_state(Gst.State.PLAYING)
        self._attach_bus_signal_watch()
        try:
            _change, state, _pending = self.pipeline.get_state(1 * Gst.SECOND)
            self.agent.add_runtime_log(f"[GST] peer={self.peer_id} pipeline state={state.value_nick}")
        except Exception:
            self.agent.add_runtime_log(f"[GST] peer={self.peer_id} pipeline PLAYING requested")

    def _attach_bus_signal_watch(self) -> None:
        if self.pipeline is None:
            return
        bus = self.pipeline.get_bus()
        if bus is None:
            return

        def on_bus_message(bus, message, loop):
            t = message.type
            if t == Gst.MessageType.ERROR:
                err, debug = message.parse_error()
                print(f"[GST ERROR] {err} | {debug}", flush=True)
            elif t == Gst.MessageType.WARNING:
                err, debug = message.parse_warning()
                print(f"[GST WARN] {err} | {debug}", flush=True)
            elif t == Gst.MessageType.STATE_CHANGED:
                if message.src == self.pipeline:
                    old, new, pending = message.parse_state_changed()
                    print(f"[GST STATE] {old.value_nick} -> {new.value_nick}", flush=True)

        bus.add_signal_watch()
        bus.connect('message', on_bus_message, None)

    def _attach_bus_watch(self) -> None:
        if self.pipeline is None:
            return
        bus = self.pipeline.get_bus()
        if bus is None:
            return

        def _bus_loop() -> None:
            while not self._bus_stop.is_set() and self.pipeline is not None:
                msg = bus.timed_pop_filtered(
                    200 * Gst.MSECOND,
                    Gst.MessageType.ERROR
                    | Gst.MessageType.WARNING
                    | Gst.MessageType.STATE_CHANGED,
                )
                if msg is None:
                    continue

                mtype = msg.type
                if mtype == Gst.MessageType.ERROR:
                    err, dbg = msg.parse_error()
                    src = msg.src.get_name() if msg.src is not None else "unknown"
                    self.agent.add_runtime_log(f"[GST][ERROR] {src}: {err}; {dbg or ''}")
                    print(f"[GST ERROR] {err} | {dbg}", flush=True)
                elif mtype == Gst.MessageType.WARNING:
                    warn, dbg = msg.parse_warning()
                    src = msg.src.get_name() if msg.src is not None else "unknown"
                    self.agent.add_runtime_log(f"[GST][WARN] {src}: {warn}; {dbg or ''}")
                    print(f"[GST WARN] {warn} | {dbg}", flush=True)
                elif mtype == Gst.MessageType.STATE_CHANGED and msg.src == self.pipeline:
                    old_state, new_state, _pending = msg.parse_state_changed()
                    self.agent.add_runtime_log(
                        f"[GST] pipeline state changed {old_state.value_nick}->{new_state.value_nick}"
                    )
                    print(f"[GST STATE] {old_state.value_nick} -> {new_state.value_nick}", flush=True)

        self._bus_thread = threading.Thread(target=_bus_loop, daemon=True)
        self._bus_thread.start()

    def _attach_media_flow_probes(self) -> None:
        if self.pipeline is None:
            return

        video_pay = self.pipeline.get_by_name("video_pay")
        if video_pay is not None:
            pad = video_pay.get_static_pad("src")
            if pad is not None:
                pad.add_probe(Gst.PadProbeType.BUFFER, self._on_video_rtp_buffer)

        audio_pay = self.pipeline.get_by_name("audio_pay")
        if audio_pay is not None:
            pad = audio_pay.get_static_pad("src")
            if pad is not None:
                pad.add_probe(Gst.PadProbeType.BUFFER, self._on_audio_rtp_buffer)

    def _on_video_rtp_buffer(self, _pad, info: Gst.PadProbeInfo):
        buf = info.get_buffer()
        if buf is None:
            return Gst.PadProbeReturn.OK

        self._video_probe_count += 1
        if self._video_probe_count == 1:
            self.agent.add_runtime_log("[VIDEO] media flow detected")
        elif self._video_probe_count % 600 == 0:
            self.agent.add_runtime_log(f"[VIDEO] frames sent={self._video_probe_count}")
        return Gst.PadProbeReturn.OK

    def _on_audio_rtp_buffer(self, _pad, info: Gst.PadProbeInfo):
        buf = info.get_buffer()
        if buf is None:
            return Gst.PadProbeReturn.OK

        self._audio_probe_count += 1
        if self._audio_probe_count == 1:
            self.agent.add_runtime_log("[AUDIO] media flow detected")
        elif self._audio_probe_count % 2000 == 0:
            self.agent.add_runtime_log(f"[AUDIO] packets sent={self._audio_probe_count}")
        return Gst.PadProbeReturn.OK

    def _on_connection_state(self, webrtc, _pspec):
        try:
            state = str(webrtc.get_property("connection-state").value_nick)
        except Exception:
            return
        self.connection_state = state
        if state == "connected":
            self.agent.stats.mark_connected_now()
        if state in {"failed", "closed", "disconnected"}:
            self.loop.call_soon_threadsafe(asyncio.create_task, self.agent._remove_peer(self.peer_id))

    def is_connected(self) -> bool:
        return self.connection_state == "connected"

    def _on_ice_candidate(self, _webrtc, mlineindex, candidate):
        if self._candidate_sender is None:
            return
        payload = {
            "type": "candidate",
            "candidate": {
                "sdpMid": "0",
                "sdpMLineIndex": int(mlineindex),
                "candidate": str(candidate),
            },
        }
        self.loop.call_soon_threadsafe(asyncio.create_task, self._candidate_sender(payload))

    def _on_data_channel(self, _webrtc, channel):
        label = str(channel.get_property("label") or "")
        if label == "input":
            self.input_channel = channel
            channel.connect("on-message-string", self._on_input_message)
        elif label == "control":
            self.control_channel = channel
            channel.connect("on-message-string", self._on_control_message)

    def _on_input_message(self, _channel, message: str):
        try:
            msg = json.loads(message)
        except Exception:
            return
        self.agent.input_worker.push(msg)

    def _send_control(self, payload: dict):
        if self.control_channel is None:
            return
        try:
            self.control_channel.emit("send-string", json.dumps(payload))
        except Exception:
            pass

    def _on_control_message(self, _channel, message: str):
        try:
            msg = json.loads(message)
        except Exception:
            return
        self.loop.call_soon_threadsafe(
            asyncio.create_task,
            self.agent._handle_control_message(msg, self._send_control),
        )

    def _fail_answer(self, message: str) -> None:
        if self._answer_ready is not None and not self._answer_ready.done():
            self.loop.call_soon_threadsafe(self._answer_ready.set_exception, RuntimeError(message))

    def _on_local_description_set(self, _promise: Gst.Promise, answer) -> None:
        if self._answer_ready is None:
            return

        local = self.webrtc.get_property("local-description")
        if local is None or getattr(local, "sdp", None) is None:
            local = answer

        if local is None or getattr(local, "sdp", None) is None:
            self._fail_answer("set-local-description produced no local SDP")
            return

        try:
            sdp_text = local.sdp.as_text()
        except Exception as exc:
            self._fail_answer(str(exc))
            return

        if not self._answer_ready.done():
            self.loop.call_soon_threadsafe(self._answer_ready.set_result, sdp_text)

    def _on_answer_created(self, promise: Gst.Promise):
        if self._answer_ready is None:
            return

        reply = promise.get_reply()
        if reply is None:
            self._fail_answer("Failed to create answer")
            return

        answer = reply.get_value("answer")
        if answer is None:
            self._fail_answer("create-answer returned no SDP answer")
            return

        local_promise = Gst.Promise.new_with_change_func(
            lambda p, *_: self._on_local_description_set(p, answer),
            None,
            None,
        )
        self.webrtc.emit("set-local-description", answer, local_promise)

    def _on_remote_description_set(self, promise: Gst.Promise) -> None:
        if self._answer_ready is None:
            return

        reply = promise.get_reply()
        if reply is None:
            self._fail_answer("Failed to set remote description")
            return

        answer_promise = Gst.Promise.new_with_change_func(lambda p, *_: self._on_answer_created(p), None, None)
        self.webrtc.emit("create-answer", None, answer_promise)

    def _apply_offer_payload_types(self, video_pt: Optional[int], audio_pt: Optional[int]) -> None:
        if self.pipeline is None:
            return

        if video_pt is not None:
            video_pay = self.pipeline.get_by_name("video_pay")
            if video_pay is not None:
                try:
                    video_pay.set_property("pt", int(video_pt))
                except Exception:
                    pass

        if audio_pt is not None:
            audio_pay = self.pipeline.get_by_name("audio_pay")
            if audio_pay is not None:
                try:
                    audio_pay.set_property("pt", int(audio_pt))
                except Exception:
                    pass

    async def _process_offer_with_sdp(self, sdp_text: str) -> str:
        res, sdp_msg = GstSdp.SDPMessage.new()
        if res != GstSdp.SDPResult.OK:
            raise RuntimeError("Failed to allocate SDP message")
        parse_res = GstSdp.sdp_message_parse_buffer(bytes(sdp_text.encode("utf-8")), sdp_msg)
        if parse_res != GstSdp.SDPResult.OK:
            raise RuntimeError("Failed to parse remote SDP offer")

        offer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.OFFER, sdp_msg)
        remote_promise = Gst.Promise.new()
        self.webrtc.emit("set-remote-description", offer, remote_promise)
        remote_promise.wait()

        answer_promise = Gst.Promise.new()
        self.webrtc.emit("create-answer", None, answer_promise)
        answer_promise.wait()

        reply = answer_promise.get_reply()
        if reply is None:
            raise RuntimeError("Failed to create answer")

        answer = reply.get_value("answer")
        if answer is None:
            raise RuntimeError("create-answer returned no SDP answer")

        local_promise = Gst.Promise.new()
        self.webrtc.emit("set-local-description", answer, local_promise)
        local_promise.wait()

        local = self.webrtc.get_property("local-description")
        if local is None or getattr(local, "sdp", None) is None:
            local = answer
        if local is None or getattr(local, "sdp", None) is None:
            raise RuntimeError("set-local-description produced no local SDP")

        return local.sdp.as_text()

    async def process_offer(self, sdp: str) -> str:
        if self.pipeline is None:
            self.start()
        filtered_sdp = keep_h264_only(sdp)
        v_pt, a_pt = select_offer_payload_types(filtered_sdp)
        self._apply_offer_payload_types(v_pt, a_pt)
        print("[SDP] filtered offer to H264-only (baseline-preferred)", flush=True)

        try:
            return await self._process_offer_with_sdp(filtered_sdp)
        except Exception as exc:
            # If filtered offer is rejected by specific browser variants, retry once with original SDP.
            if filtered_sdp != sdp:
                print(f"[SDP] filtered offer failed ({exc}); retrying with original offer", flush=True)
                return await self._process_offer_with_sdp(sdp)
            raise

    def add_ice_candidate(self, candidate: dict) -> None:
        if self.webrtc is None:
            return
        cand = str(candidate.get("candidate", "") or "").strip()
        if not cand:
            return
        try:
            mline = int(candidate.get("sdpMLineIndex", 0) or 0)
        except Exception:
            mline = 0
        try:
            self.webrtc.emit("add-ice-candidate", mline, cand)
        except Exception:
            pass

    async def close(self):
        self.agent.input_worker.reset()
        if self.pipeline is not None:
            self._bus_stop.set()
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        if self._bus_thread is not None and self._bus_thread.is_alive():
            self._bus_thread.join(timeout=0.5)


class WarpDeskPyAgent:
    def __init__(self):
        self.sessions: Dict[str, Session] = {}
        self._session_lock = threading.Lock()
        self.peers: Dict[str, GstPeer] = {}
        self.cfg = RuntimeConfig()
        self.stats = RuntimeStats()
        self.input_worker = InputWorker(self.stats)
        self.local_ip = get_local_ip()
        self._auth_lock = threading.Lock()
        self.username = USERNAME
        self.password = PASSWORD
        self._login_log: list[LoginEntry] = []
        self._shutdown_requested = threading.Event()
        self._tui_stop = threading.Event()
        self._tui_thread = None
        self._tui_input_thread = None
        self._runtime_log_lock = threading.Lock()
        self._runtime_log: list[str] = []
        self.ice_servers = build_ice_servers()
        self._cf_turn_enabled = (
            CLOUDFLARE_TURN_ENABLED and bool(CLOUDFLARE_TURN_TOKEN_ID and CLOUDFLARE_TURN_API_TOKEN)
        )
        self._cf_cached_ice_servers: list[dict] = []
        self._cf_cached_until = 0.0
        self._cf_lock = asyncio.Lock()

        if CLOUDFLARE_TURN_ENABLED and not self._cf_turn_enabled:
            self.add_runtime_log("[TURN] CLOUDFLARE_TURN enabled but token id/api token missing; using static ICE config")

        if ENABLE_TUI:
            self._start_tui()

    def _get_auth(self) -> tuple[str, str]:
        with self._auth_lock:
            return self.username, self.password

    def _set_username(self, value: str) -> None:
        with self._auth_lock:
            self.username = value

    def _set_password(self, value: str) -> None:
        with self._auth_lock:
            self.password = value

    def _regenerate_password(self, length: int = 8) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
        new_password = "".join(secrets.choice(alphabet) for _ in range(max(4, length)))
        self._set_password(new_password)
        return new_password

    def _add_login_entry(self, username: str, ip: str) -> None:
        entry = LoginEntry(ip=ip, username=username, time=datetime.now().strftime("%H:%M:%S"))
        with self._auth_lock:
            self._login_log.append(entry)
            if len(self._login_log) > 100:
                self._login_log = self._login_log[-100:]

    def _snapshot_login_log(self) -> list[LoginEntry]:
        with self._auth_lock:
            return list(self._login_log)

    def add_runtime_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"{stamp} {message}"
        with self._runtime_log_lock:
            self._runtime_log.append(line)
            if len(self._runtime_log) > 200:
                self._runtime_log = self._runtime_log[-200:]
        print(line)

    def _snapshot_runtime_log(self, limit: int = 6) -> list[str]:
        with self._runtime_log_lock:
            return list(self._runtime_log[-limit:])

    def _session_count(self) -> int:
        with self._session_lock:
            return len(self.sessions)

    def _request_shutdown(self) -> None:
        self._shutdown_requested.set()
        self._tui_stop.set()
        # Raise KeyboardInterrupt in the main thread to stop web.run_app.
        _thread.interrupt_main()

    async def _remove_peer(self, peer_id: str):
        peer = self.peers.pop(peer_id, None)
        if peer is not None:
            await peer.close()
        self.stats.set_active_peers(len(self.peers))
        self.stats.clear_connected_since_if_no_peers()

    async def _handle_control_message(self, msg: dict, send_control):
        t = msg.get("type")
        req_id = msg.get("request_id")
        if t == "settings":
            fps = int(msg.get("fps", self.cfg.target_fps))
            scale = int(msg.get("scale", self.cfg.scale))
            self.cfg.target_fps = max(10, min(MAX_FPS, fps))
            self.cfg.scale = max(25, min(100, scale))
            send_control({
                "type": "settings_applied",
                "request_id": req_id,
                "fps": self.cfg.target_fps,
                "scale": self.cfg.scale,
            })
            return

        if t == "settings_get":
            send_control({
                "type": "settings_applied",
                "request_id": req_id,
                "fps": int(self.cfg.target_fps),
                "scale": int(self.cfg.scale),
                "max_fps": int(MAX_FPS),
            })
            return

        try:
            if t == "clip_read":
                text = ""
                try:
                    text = pyperclip.paste() or ""
                except Exception:
                    text = ""
                if len(text) > 64000:
                    text = text[:64000]
                send_control({
                    "type": "clip_data",
                    "request_id": req_id,
                    "text": text,
                })
            elif t == "clip_write":
                try:
                    text = str(msg.get("text", ""))[:64000]
                    pyperclip.copy(text)
                    send_control({
                        "type": "clip_written",
                        "request_id": req_id,
                        "ok": True,
                    })
                except Exception:
                    send_control({
                        "type": "clip_written",
                        "request_id": req_id,
                        "ok": False,
                    })
            elif t == "cmd":
                command = str(msg.get("command", ""))[:2000]
                if not command:
                    return
                send_control({
                    "type": "cmd_started",
                    "request_id": req_id,
                })
                shell_exe = os.environ.get("COMSPEC", "cmd.exe") if os.name == "nt" else None
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(Path(__file__).resolve().parent),
                    executable=shell_exe,
                )
                out, _ = await proc.communicate()
                text = (out or b"").decode(errors="replace")
                if len(text) > 12000:
                    text = text[-12000:]
                send_control({
                    "type": "cmd_output",
                    "request_id": req_id,
                    "exit_code": int(proc.returncode or 0),
                    "output": text,
                })
        except Exception:
            send_control({
                "type": "control_error",
                "request_id": req_id,
                "error": "control_operation_failed",
            })

    def _start_tui(self):
        edit_mode = {"value": "none"}
        edit_buf = {"value": ""}
        edit_lock = threading.Lock()

        def handle_key(ch: str):
            if not ch:
                return
            with edit_lock:
                mode = edit_mode["value"]
                buf = edit_buf["value"]

                if mode == "none":
                    k = ch.lower()
                    if k == "q":
                        self._request_shutdown()
                    elif k == "r":
                        self._regenerate_password(8)
                    elif k == "u":
                        edit_mode["value"] = "username"
                        edit_buf["value"] = ""
                    elif k == "p":
                        edit_mode["value"] = "password"
                        edit_buf["value"] = ""
                    return

                if ch in {"\r", "\n"}:
                    if buf:
                        if mode == "username":
                            self._set_username(buf)
                        else:
                            self._set_password(buf)
                    edit_mode["value"] = "none"
                    edit_buf["value"] = ""
                    return

                if ch == "\x1b":
                    edit_mode["value"] = "none"
                    edit_buf["value"] = ""
                    return

                if ch in {"\x08", "\x7f"}:
                    if buf:
                        edit_buf["value"] = buf[:-1]
                    return

                if ch.isprintable() and len(buf) < 32:
                    edit_buf["value"] = buf + ch

        def input_loop():
            if os.name != "nt":
                return
            import msvcrt

            while not self._tui_stop.is_set():
                if not msvcrt.kbhit():
                    time.sleep(0.05)
                    continue
                try:
                    key = msvcrt.getwch()
                except Exception:
                    continue
                handle_key(key)

        def render_plain(_last_snap: dict, started_at: float):
            while not self._tui_stop.is_set():
                snap = self.stats.snapshot()
                elapsed = max(0.001, time.time() - started_at)
                cap_fps = snap["capture_frames"] / elapsed
                send_fps = snap["sent_frames"] / elapsed
                username, password = self._get_auth()
                print(
                    f"[WarpDesk agent] peers={snap['active_peers']} cap_fps={cap_fps:.1f} "
                    f"send_fps={send_fps:.1f} mode={snap['capture_mode']} rgb_max={snap['last_rgb_max']} "
                    f"user={username} pass={password} input_drops={snap['input_drops']} "
                    f"audio_drops={snap['audio_drops']}"
                )
                time.sleep(1.0)

        def render_rich(started_at: float):
            console = Console(force_terminal=True)

            def build_panel() -> Panel:
                snap = self.stats.snapshot()
                elapsed = max(0.001, time.time() - started_at)
                cap_fps = snap["capture_frames"] / elapsed
                send_fps = snap["sent_frames"] / elapsed
                username, password = self._get_auth()
                with edit_lock:
                    current_mode = edit_mode["value"]
                    current_buf = edit_buf["value"]

                if current_mode == "username":
                    user_line = f"[dim]  User           [/][bold yellow]{current_buf}█[/][dim]  (Enter to save, Esc to cancel)[/]"
                else:
                    user_line = f"[dim]  User           [/][bold white]{username}[/]"

                if current_mode == "password":
                    pass_line = f"[dim]  Password       [/][bold yellow]{current_buf}█[/][dim]  (Enter to save, Esc to cancel)[/]"
                else:
                    pass_line = f"[dim]  Password       [/][bold yellow]{password}[/]"

                lines = [
                    "[dim]  Status: [/][bold green]● Running[/]",
                    "",
                    "[dim]  ── Connection ─────────────────────[/]",
                    "",
                    f"[dim]  Local Access   [/][bold cyan]https://localhost:{PORT}[/]",
                    f"[dim]  LAN IP         [/][bold cyan]https://{self.local_ip}:{PORT}[/]",
                    "",
                    "[dim]  ── Authentication ─────────────────[/]",
                    "",
                    user_line,
                    pass_line,
                    "",
                    "[dim]  ── Sessions ───────────────────────[/]",
                    "",
                    f"[dim]  API Logins     [/][bold white]{self._session_count()}[/]",
                    f"[dim]  WebRTC Streams [/][bold white]{snap['active_peers']}[/]",
                    "",
                    "[dim]  ── Stream Stats ───────────────────[/]",
                    "",
                    f"[dim]  Target FPS     [/][bold white]{self.cfg.target_fps}[/]",
                    f"[dim]  Scale          [/][bold white]{self.cfg.scale}%[/]",
                    f"[dim]  Capture FPS    [/][bold white]{cap_fps:.1f}[/]",
                    f"[dim]  Send FPS       [/][bold white]{send_fps:.1f}[/]",
                    f"[dim]  Capture Mode   [/][bold white]{snap['capture_mode']}[/]",
                    f"[dim]  RGB Max        [/][bold white]{snap['last_rgb_max']}[/]",
                    f"[dim]  Input Drops    [/][bold white]{snap['input_drops']}[/]",
                    f"[dim]  Audio Drops    [/][bold white]{snap['audio_drops']}[/]",
                    f"[dim]  Capture Errors [/][bold white]{snap['capture_failures']}[/]",
                ]

                recent = self._snapshot_login_log()[-5:]
                if recent:
                    lines.extend([
                        "",
                        "[dim]  ── Recent Logins ──────────────────[/]",
                        "",
                    ])
                    for entry in reversed(recent):
                        lines.append(
                            f"  [dim]{entry.time}[/]  [bold cyan]{entry.username}[/] [dim]from[/] [bold white]{entry.ip}[/]"
                        )

                recent_runtime = self._snapshot_runtime_log(6)
                if recent_runtime:
                    lines.extend([
                        "",
                        "[dim]  ── Runtime Logs ───────────────────[/]",
                        "",
                    ])
                    for item in recent_runtime:
                        lines.append(f"  [dim]{item}[/]")

                lines.extend([
                    "",
                    "[dim]  ── Controls ───────────────────────[/]",
                    "",
                    "  [bold magenta]U[/]  [white]Set username[/]       [bold magenta]P[/]  [white]Set password[/]",
                    "  [bold magenta]R[/]  [white]Regenerate password[/]",
                    "  [bold magenta]Q[/]  [white]Quit[/]",
                ])

                return Panel(
                    Text.from_markup("\n".join(lines)),
                    title="[bold cyan] ◈ [/][bold white]WarpDesk Agent[/] [dim]v0.2.0[/]",
                    border_style="bold cyan",
                )

            try:
                with Live(console=console, refresh_per_second=4, screen=True) as live:
                    while not self._tui_stop.is_set():
                        live.update(build_panel())
                        time.sleep(0.25)
            except Exception:
                render_plain({}, started_at)

        started = time.time()
        rich_available = HAS_RICH and (FORCE_RICH_TUI or sys.stdout.isatty())
        if rich_available:
            self._tui_thread = threading.Thread(target=render_rich, args=(started,), daemon=True)
        else:
            self._tui_thread = threading.Thread(target=render_plain, args=({}, started), daemon=True)
        self._tui_thread.start()

        self._tui_input_thread = threading.Thread(target=input_loop, daemon=True)
        self._tui_input_thread.start()

    def _auth_ok(self, request: web.Request) -> bool:
        now = time.time()
        with self._session_lock:
            stale = [
                token for token, session in self.sessions.items()
                if now - session.created_at > SESSION_TTL_SECONDS
            ]
            for token in stale:
                self.sessions.pop(token, None)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        token = auth[7:]
        with self._session_lock:
            return token in self.sessions

    def _auth_ok_ws(self, request: web.Request) -> bool:
        token = request.query.get("token", "")
        if not token:
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:]
        if not token:
            return False
        with self._session_lock:
            return token in self.sessions

    def _connected_peer_count(self) -> int:
        return sum(1 for p in self.peers.values() if p.is_connected())

    async def health(self, _request: web.Request):
        snap = self.stats.snapshot()
        return web.json_response({
            "ok": True,
            "active_peers": self._connected_peer_count(),
            "client_ip": snap["last_client_ip"],
            "connected_since": snap["connected_since"],
            "target_fps": self.cfg.target_fps,
            "scale": self.cfg.scale,
            "capture_mode": snap["capture_mode"],
            "capture_failures": snap["capture_failures"],
            "input_drops": snap["input_drops"],
            "audio_drops": snap["audio_drops"],
        })

    async def login(self, request: web.Request):
        body = await request.json()
        username = body.get("username", "")
        password = body.get("password", "")
        expected_user, expected_pass = self._get_auth()
        if username != expected_user or password != expected_pass:
            return web.json_response({"success": False, "error": "Invalid credentials"}, status=401)

        token = secrets.token_urlsafe(24)
        with self._session_lock:
            self.sessions[token] = Session(username=username, created_at=time.time())
        client_ip = request.headers.get("x-forwarded-for") or request.remote or "unknown"
        self._add_login_entry(username=username, ip=client_ip)
        return web.json_response({"success": True, "token": token, "username": username})

    async def validate_session(self, request: web.Request):
        if not self._auth_ok(request):
            return web.json_response({"valid": False})
        return web.json_response({"valid": True})

    async def device_info(self, request: web.Request):
        if not self._auth_ok(request):
            return web.json_response({"success": False, "error": "Unauthorized"}, status=401)
        return web.json_response({
            "success": True,
            "device_name": socket.gethostname(),
            "os": os.name,
        })

    async def _fetch_cloudflare_turn_ice_servers(self) -> Optional[list[dict]]:
        if not self._cf_turn_enabled:
            return None

        endpoint = (
            f"{CLOUDFLARE_TURN_API_BASE}/turn/keys/"
            f"{CLOUDFLARE_TURN_TOKEN_ID}/credentials/generate-ice-servers"
        )
        headers = {
            "Authorization": f"Bearer {CLOUDFLARE_TURN_API_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {"ttl": max(60, CLOUDFLARE_TURN_TTL_SECONDS)}

        timeout = ClientTimeout(total=max(2.0, CLOUDFLARE_TURN_TIMEOUT_SECONDS))
        async with ClientSession(timeout=timeout) as session:
            async with session.post(endpoint, headers=headers, json=payload) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    self.add_runtime_log(f"[TURN] Cloudflare API error status={resp.status} body={body[:180]}")
                    return None
                data = await resp.json(content_type=None)

        # Cloudflare can return either top-level iceServers or wrapped result. Support both.
        candidate = data.get("iceServers") if isinstance(data, dict) else None
        if not candidate and isinstance(data, dict):
            result = data.get("result")
            if isinstance(result, dict):
                candidate = result.get("iceServers")

        if not isinstance(candidate, list):
            return None
        valid = [item for item in candidate if isinstance(item, dict) and item.get("urls")]
        return valid or None

    async def _get_effective_ice_servers(self) -> list[dict]:
        if not self._cf_turn_enabled:
            return self.ice_servers

        now = time.time()
        if self._cf_cached_ice_servers and now < self._cf_cached_until:
            return self._cf_cached_ice_servers

        async with self._cf_lock:
            now = time.time()
            if self._cf_cached_ice_servers and now < self._cf_cached_until:
                return self._cf_cached_ice_servers

            fresh = await self._fetch_cloudflare_turn_ice_servers()
            if fresh:
                # Refresh a little before true expiry so clients avoid edge-expired creds.
                cache_seconds = max(30, CLOUDFLARE_TURN_TTL_SECONDS - 30)
                self._cf_cached_ice_servers = fresh
                self._cf_cached_until = now + cache_seconds
                return fresh

            # Fallback to static ICE config if Cloudflare API is unavailable.
            if self._cf_cached_ice_servers:
                return self._cf_cached_ice_servers
            return self.ice_servers

    async def ice_servers_config(self, request: web.Request):
        if not self._auth_ok(request):
            return web.json_response({"success": False, "error": "Unauthorized"}, status=401)
        effective = await self._get_effective_ice_servers()
        return web.json_response({
            "success": True,
            "iceServers": effective,
        })

    async def get_settings(self, request: web.Request):
        if not self._auth_ok(request):
            return web.json_response({"success": False, "error": "Unauthorized"}, status=401)
        return web.json_response({
            "success": True,
            "fps": int(self.cfg.target_fps),
            "scale": int(self.cfg.scale),
            "max_fps": int(MAX_FPS),
        })

    async def update_settings(self, request: web.Request):
        if not self._auth_ok(request):
            return web.json_response({"success": False, "error": "Unauthorized"}, status=401)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"success": False, "error": "invalid_json"}, status=400)

        try:
            fps = int(body.get("fps", self.cfg.target_fps))
            scale = int(body.get("scale", self.cfg.scale))
        except Exception:
            return web.json_response({"success": False, "error": "invalid_settings"}, status=400)

        self.cfg.target_fps = max(10, min(MAX_FPS, fps))
        self.cfg.scale = max(25, min(100, scale))
        return web.json_response({
            "success": True,
            "fps": int(self.cfg.target_fps),
            "scale": int(self.cfg.scale),
            "max_fps": int(MAX_FPS),
        })

    async def update_auth(self, request: web.Request):
        if not self._auth_ok(request):
            return web.json_response({"success": False, "error": "Unauthorized"}, status=401)

        body = await request.json()
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))

        if not username or not password:
            return web.json_response({"success": False, "error": "username/password required"}, status=400)

        self._set_username(username)
        self._set_password(password)
        self.add_runtime_log("[AUTH] credentials updated from launcher")
        return web.json_response({"success": True, "username": username})

    async def offer(self, request: web.Request):
        if not self._auth_ok(request):
            return web.Response(status=401, text="Unauthorized")

        if len(self.peers) >= MAX_SESSIONS:
            return web.Response(status=429, text="Too many active sessions")

        payload = await request.json()
        sdp = payload.get("sdp")
        if not sdp:
            return web.Response(status=400, text="Missing SDP")
        if gi is None:
            return web.Response(status=500, text="GStreamer Python bindings (gi) are not available")

        peer_id = secrets.token_urlsafe(12)
        peer = GstPeer(self, peer_id)
        try:
            answer_sdp = await peer.process_offer(sdp)
        except Exception as e:
            await peer.close()
            return web.Response(status=500, text=f"Failed to process WebRTC offer via GStreamer: {e}")

        self.peers[peer_id] = peer
        self.stats.set_active_peers(len(self.peers))
        tuned_sdp = tune_answer_sdp(answer_sdp, int(self.cfg.target_fps), VIDEO_MAX_BITRATE_BPS)
        return web.json_response({"sdp": tuned_sdp})

    async def ws_signaling(self, request: web.Request):
        print('[WS] new websocket connection opened', flush=True)
        forwarded_for = str(request.headers.get("X-Forwarded-For", "")).strip()
        remote_ip = forwarded_for.split(",")[0].strip() if forwarded_for else (request.remote or "unknown")
        self.stats.set_last_client_ip(remote_ip)
        if not self._auth_ok_ws(request):
            return web.Response(status=401, text="Unauthorized")

        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)

        peer_id = None
        peer: Optional[GstPeer] = None
        pending_candidates: list[dict] = []

        async def send_json(payload: dict) -> None:
            if not ws.closed:
                await ws.send_json(payload)

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except Exception:
                        await send_json({"type": "error", "error": "invalid_json"})
                        continue

                    t = str(data.get("type", "")).strip().lower()
                    if t == "offer":
                        print('[OFFER] processing offer, about to add tracks', flush=True)
                        sdp = data.get("sdp")
                        if not sdp:
                            await send_json({"type": "error", "error": "missing_sdp"})
                            continue

                        if peer is not None and peer_id is not None:
                            await self._remove_peer(peer_id)

                        if len(self.peers) >= MAX_SESSIONS:
                            await send_json({"type": "error", "error": "too_many_sessions"})
                            continue

                        peer_id = secrets.token_urlsafe(12)
                        peer = GstPeer(self, peer_id, candidate_sender=send_json)
                        try:
                            answer_sdp = await peer.process_offer(sdp)
                            for cand in pending_candidates:
                                peer.add_ice_candidate(cand)
                            pending_candidates.clear()
                        except Exception as e:
                            await peer.close()
                            peer = None
                            peer_id = None
                            await send_json({"type": "error", "error": f"offer_failed: {e}"})
                            continue

                        self.peers[peer_id] = peer
                        self.stats.set_active_peers(len(self.peers))
                        tuned_sdp = tune_answer_sdp(answer_sdp, int(self.cfg.target_fps), VIDEO_MAX_BITRATE_BPS)
                        await send_json({"type": "answer", "sdp": tuned_sdp})
                        continue

                    if t == "candidate":
                        candidate = data.get("candidate") or {}
                        if not isinstance(candidate, dict):
                            await send_json({"type": "error", "error": "invalid_candidate"})
                            continue
                        if peer is None:
                            pending_candidates.append(candidate)
                        else:
                            peer.add_ice_candidate(candidate)
                        continue

                    await send_json({"type": "error", "error": "unknown_message_type"})

                elif msg.type == web.WSMsgType.ERROR:
                    break
        finally:
            if peer_id is not None:
                await self._remove_peer(peer_id)

        return ws

    async def close(self):
        self._tui_stop.set()
        self.input_worker.reset()
        to_close = list(self.peers.values())
        for peer in to_close:
            await peer.close()
        self.peers = {}
        self.stats.set_active_peers(0)
        self.input_worker.stop()


def cert_paths() -> tuple[Path, Path]:
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
    cert_dir = home / ".opendesk"
    return cert_dir / "cert.pem", cert_dir / "key.pem"


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(request)

    origin = request.headers.get("Origin", "*")
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Authorization,Content-Type"
    response.headers["Access-Control-Max-Age"] = "86400"
    response.headers["Vary"] = "Origin"
    return response


async def options_handler(_request: web.Request):
    return web.Response(status=204)


def create_app() -> web.Application:
    agent = WarpDeskPyAgent()
    app = web.Application(client_max_size=2 * 1024 * 1024, middlewares=[cors_middleware])

    app.add_routes([
        web.options("/api/{tail:.*}", options_handler),
        web.get("/api/health", agent.health),
        web.post("/api/login", agent.login),
        web.get("/api/session", agent.validate_session),
        web.get("/api/device-info", agent.device_info),
        web.get("/api/ice-servers", agent.ice_servers_config),
        web.get("/api/settings", agent.get_settings),
        web.post("/api/settings", agent.update_settings),
        web.post("/api/auth/update", agent.update_auth),
        web.post("/api/webrtc/offer", agent.offer),
        web.get("/ws", agent.ws_signaling),
    ])

    web_root_env = os.getenv("WARPDESK_WEB_ROOT", "").strip()
    web_root_candidates = []
    if web_root_env:
        web_root_candidates.append(Path(web_root_env))
    web_root_candidates.append(Path.cwd() / "web")
    web_root_candidates.append(Path(__file__).resolve().parent.parent / "web")
    if getattr(sys, "_MEIPASS", None):
        web_root_candidates.append(Path(sys._MEIPASS) / "web")

    web_root = next((p for p in web_root_candidates if p.exists() and p.is_dir()), None)
    if web_root is not None:
        app.router.add_static("/", str(web_root), show_index=False)
    else:
        logger.warning("No web root found; static routes are disabled")

    async def on_cleanup(_app):
        await agent.close()

    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    app = create_app()
    cert, key = cert_paths()

    ssl_ctx: Optional[ssl.SSLContext] = None
    if cert.exists() and key.exists():
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(str(cert), str(key))

    scheme = "https" if ssl_ctx else "http"
    initial_user = USERNAME
    initial_pass = PASSWORD
    print(f"WarpDesk agent listening on {scheme}://0.0.0.0:{PORT}")
    print(f"Login: {initial_user} / {initial_pass}")
    if not ssl_ctx:
        print("TLS cert not found in %USERPROFILE%\\.opendesk. Running without TLS.")

    try:
        web.run_app(app, host="0.0.0.0", port=PORT, ssl_context=ssl_ctx, access_log=None, print=None)
    except KeyboardInterrupt:
        print("WarpDesk agent shutting down...")


if __name__ == "__main__":
    main()
