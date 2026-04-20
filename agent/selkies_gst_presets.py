"""
Selkies-inspired GStreamer pipeline presets.

These strings are intended for future direct GStreamer integration and are
kept here so the Python backend can follow Selkies media-path choices.
"""

import os
import platform
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class PipelinePreset:
    name: str
    video: str
    audio: str


def _clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _scaled_dim(base: int, scale: int) -> int:
    raw = max(2, (int(base) * int(scale)) // 100)
    if scale >= 100:
        return raw
    # Hardware encoders are less error-prone with scaled dimensions aligned to 16.
    aligned = (raw // 16) * 16
    if aligned >= 16:
        return aligned
    return max(2, raw & ~1)


def _tuned_capture_caps(fps: int) -> str:
    fps = _clamp_int(fps, 10, 120)
    return f"video/x-raw(memory:D3D11Memory),framerate={fps}/1,width=1920,height=1080"


def _tuned_encoder_input_caps(fps: int, scale: int) -> str:
    _ = _clamp_int(fps, 10, 120)
    scale = _clamp_int(scale, 25, 100)
    width = _scaled_dim(1920, scale)
    height = _scaled_dim(1080, scale)
    return f"video/x-raw(memory:D3D11Memory),format=NV12,width={width},height={height}"


def _apply_runtime_tuning(video_branch: str, fps: int, scale: int) -> str:
    base_capture_caps = "video/x-raw(memory:D3D11Memory),framerate=60/1,width=1920,height=1080"
    tuned_capture_caps = _tuned_capture_caps(fps)
    tuned = video_branch.replace(base_capture_caps, tuned_capture_caps)

    base_encoder_caps = "video/x-raw(memory:D3D11Memory),format=NV12"
    if base_encoder_caps in tuned:
        return tuned.replace(base_encoder_caps, _tuned_encoder_input_caps(fps, scale))

    # Software fallback path keeps source caps stable; avoid source negotiation churn.
    return tuned


WINDOWS_MF_H264 = PipelinePreset(
    name="windows-mf-h264-lowlatency",
    video=(
        "d3d11screencapturesrc show-cursor=true do-timestamp=true ! "
        "video/x-raw(memory:D3D11Memory),framerate=60/1,width=1920,height=1080 ! "
        "d3d11colorconvert ! "
        "d3d11convert ! "
        "video/x-raw(memory:D3D11Memory),format=NV12 ! "
        "mfh264enc bitrate=20000 low-latency=true cabac=false bframes=0 ! "
        "video/x-h264,profile=baseline,stream-format=byte-stream ! "
        "h264parse config-interval=-1 ! rtph264pay name=video_pay pt=96 config-interval=-1"
    ),
    audio=(
        "wasapi2src loopback=true low-latency=true ! "
        "queue max-size-time=200000000 ! "
        "audio/x-raw,rate=48000,channels=2 ! "
        "audioconvert ! audioresample ! "
        "opusenc bitrate=128000 bandwidth=fullband frame-size=10 ! rtpopuspay name=audio_pay pt=97"
    ),
)

WINDOWS_QSV_H264 = PipelinePreset(
    name="windows-qsv-h264-lowlatency",
    video=(
        "d3d11screencapturesrc show-cursor=true do-timestamp=true ! "
        "video/x-raw(memory:D3D11Memory),framerate=60/1,width=1920,height=1080 ! "
        "d3d11colorconvert ! "
        "d3d11convert ! "
        "video/x-raw(memory:D3D11Memory),format=NV12 ! "
        "qsvh264enc bitrate=20000 low-latency=true ! "
        "video/x-h264,profile=baseline,stream-format=byte-stream ! "
        "h264parse config-interval=-1 ! rtph264pay name=video_pay pt=96 config-interval=-1"
    ),
    audio=(
        "wasapisrc loopback=true low-latency=true ! "
        "queue max-size-time=200000000 ! "
        "audio/x-raw,rate=48000,channels=2 ! "
        "audioconvert ! audioresample ! "
        "opusenc bitrate=128000 bandwidth=fullband frame-size=10 ! rtpopuspay name=audio_pay pt=97"
    ),
)

WINDOWS_SW_H264 = PipelinePreset(
    name="windows-sw-h264-fallback",
    video=(
        "d3d11screencapturesrc show-cursor=true do-timestamp=true ! "
        "video/x-raw(memory:D3D11Memory),framerate=60/1,width=1920,height=1080 ! "
        "d3d11download ! videoconvert ! "
        "x264enc tune=zerolatency speed-preset=ultrafast key-int-max=30 bitrate=12000 byte-stream=true ! "
        "h264parse config-interval=-1 ! rtph264pay name=video_pay pt=96 config-interval=-1"
    ),
    audio=(
        "wasapi2src loopback=true low-latency=true ! "
        "queue max-size-time=200000000 ! "
        "audio/x-raw,rate=48000,channels=2 ! "
        "audioconvert ! audioresample ! "
        "opusenc bitrate=128000 bandwidth=fullband frame-size=10 ! rtpopuspay name=audio_pay pt=97"
    ),
)

LINUX_X264 = PipelinePreset(
    name="linux-x264-lowlatency",
    video=(
        "ximagesrc use-damage=false show-pointer=true ! "
        "queue leaky=downstream max-size-buffers=2 ! "
        "videoconvert ! video/x-raw,framerate=60/1 ! "
        "x264enc tune=zerolatency speed-preset=ultrafast key-int-max=30 "
        "bitrate=12000 byte-stream=true ! h264parse config-interval=-1"
    ),
    audio=(
        "pulsesrc do-timestamp=true ! audio/x-raw,rate=48000,channels=2 ! "
        "opusenc bitrate=128000 frame-size=20"
    ),
)

LINUX_NVENC_H264 = PipelinePreset(
    name="linux-nvenc-h264-lowlatency",
    video=(
        "ximagesrc use-damage=false show-pointer=true ! "
        "queue leaky=downstream max-size-buffers=2 ! "
        "videoconvert ! video/x-raw,framerate=60/1 ! "
        "nvh264enc bitrate=12000 gop-size=30 rc-mode=cbr ! "
        "h264parse config-interval=-1"
    ),
    audio=(
        "pulsesrc do-timestamp=true ! audio/x-raw,rate=48000,channels=2 ! "
        "opusenc bitrate=128000 frame-size=20"
    ),
)

LINUX_VAAPI_H264 = PipelinePreset(
    name="linux-vaapi-h264-lowlatency",
    video=(
        "ximagesrc use-damage=false show-pointer=true ! "
        "queue leaky=downstream max-size-buffers=2 ! "
        "videoconvert ! video/x-raw,framerate=60/1 ! "
        "vaapih264enc bitrate=12000 keyframe-period=30 ! "
        "h264parse config-interval=-1"
    ),
    audio=(
        "pulsesrc do-timestamp=true ! audio/x-raw,rate=48000,channels=2 ! "
        "opusenc bitrate=128000 frame-size=20"
    ),
)

MAC_VT_H264 = PipelinePreset(
    name="macos-videotoolbox-h264-lowlatency",
    video=(
        "avfvideosrc capture-screen=true capture-screen-cursor=true ! "
        "videoconvert ! video/x-raw,framerate=60/1 ! "
        "vtenc_h264_hw allow-frame-reordering=false realtime=true max-keyframe-interval=30 bitrate=12000 ! "
        "h264parse config-interval=-1"
    ),
    audio=(
        "osxaudiosrc ! audio/x-raw,rate=48000,channels=2 ! "
        "audioconvert ! audioresample ! "
        "opusenc bitrate=128000 frame-size=20"
    ),
)

MAC_X264 = PipelinePreset(
    name="macos-x264-fallback",
    video=(
        "avfvideosrc capture-screen=true capture-screen-cursor=true ! "
        "videoconvert ! video/x-raw,framerate=60/1 ! "
        "x264enc tune=zerolatency speed-preset=ultrafast key-int-max=30 bitrate=12000 byte-stream=true ! "
        "h264parse config-interval=-1"
    ),
    audio=(
        "osxaudiosrc ! audio/x-raw,rate=48000,channels=2 ! "
        "audioconvert ! audioresample ! "
        "opusenc bitrate=128000 frame-size=20"
    ),
)

def choose_windows_preset(target_fps: Optional[int] = None, scale: Optional[int] = None) -> PipelinePreset:
    enc = os.getenv("WEBRTC_ENCODER", "mf").strip().lower()
    fps = int(target_fps if target_fps is not None else os.getenv("WARPDESK_FPS", "60"))
    scl = int(scale if scale is not None else os.getenv("WARPDESK_SCALE", "100"))

    if enc == "qsv":
        preset = WINDOWS_QSV_H264
    elif enc == "sw":
        preset = WINDOWS_SW_H264
    else:
        preset = WINDOWS_MF_H264

    return PipelinePreset(
        name=preset.name,
        video=_apply_runtime_tuning(preset.video, fps, scl),
        audio=preset.audio,
    )


def _pick_available(
    candidates: list[tuple[str, PipelinePreset]],
    available_elements: Optional[Iterable[str]],
) -> PipelinePreset:
    available = {str(x).strip().lower() for x in (available_elements or []) if str(x).strip()}
    if not available:
        return candidates[0][1]

    for element, preset in candidates:
        if element.lower() in available:
            return preset
    return candidates[-1][1]


def choose_platform_preset(
    target_fps: Optional[int] = None,
    scale: Optional[int] = None,
    platform_name: Optional[str] = None,
    available_elements: Optional[Iterable[str]] = None,
) -> PipelinePreset:
    enc = os.getenv("WEBRTC_ENCODER", "auto").strip().lower()
    fps = int(target_fps if target_fps is not None else os.getenv("WARPDESK_FPS", "60"))
    scl = int(scale if scale is not None else os.getenv("WARPDESK_SCALE", "100"))

    sys_name = (platform_name or platform.system()).strip().lower()

    if sys_name.startswith("win"):
        selected = _pick_available(
            [
                ("mfh264enc", WINDOWS_MF_H264),
                ("qsvh264enc", WINDOWS_QSV_H264),
                ("x264enc", WINDOWS_SW_H264),
            ],
            available_elements,
        )
        if enc == "qsv":
            selected = _pick_available([("qsvh264enc", WINDOWS_QSV_H264), ("x264enc", WINDOWS_SW_H264)], available_elements)
        elif enc in {"sw", "software", "x264"}:
            selected = _pick_available([("x264enc", WINDOWS_SW_H264)], available_elements)
        elif enc == "mf":
            selected = _pick_available([("mfh264enc", WINDOWS_MF_H264), ("qsvh264enc", WINDOWS_QSV_H264), ("x264enc", WINDOWS_SW_H264)], available_elements)

        return PipelinePreset(
            name=selected.name,
            video=_apply_runtime_tuning(selected.video, fps, scl),
            audio=selected.audio,
        )

    if sys_name.startswith("darwin") or sys_name.startswith("mac"):
        if enc in {"sw", "software", "x264"}:
            selected = _pick_available([("x264enc", MAC_X264)], available_elements)
        else:
            selected = _pick_available(
                [("vtenc_h264_hw", MAC_VT_H264), ("x264enc", MAC_X264)],
                available_elements,
            )
        return selected

    # Linux and other POSIX-like hosts: prefer HW acceleration, then x264 fallback.
    if enc in {"nvenc", "nvidia"}:
        selected = _pick_available([("nvh264enc", LINUX_NVENC_H264), ("x264enc", LINUX_X264)], available_elements)
    elif enc in {"vaapi", "intel", "amd"}:
        selected = _pick_available([("vaapih264enc", LINUX_VAAPI_H264), ("x264enc", LINUX_X264)], available_elements)
    elif enc in {"sw", "software", "x264"}:
        selected = _pick_available([("x264enc", LINUX_X264)], available_elements)
    else:
        selected = _pick_available(
            [
                ("nvh264enc", LINUX_NVENC_H264),
                ("vaapih264enc", LINUX_VAAPI_H264),
                ("x264enc", LINUX_X264),
            ],
            available_elements,
        )

    return selected


ALL_PRESETS = [
    WINDOWS_MF_H264,
    WINDOWS_QSV_H264,
    WINDOWS_SW_H264,
    LINUX_NVENC_H264,
    LINUX_VAAPI_H264,
    LINUX_X264,
    MAC_VT_H264,
    MAC_X264,
]
