import json
import os
import platform
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import shutil
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.error import URLError
from urllib.request import Request, urlopen

from rich.console import Console
from rich import box
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.text import Text


console = Console()
LOG_LINES = deque(maxlen=240)
LAUNCHER_CONFIG_PATH = Path(__file__).resolve().parent / "launcher_config.json"
BACKEND_LOG_PATH = Path(__file__).resolve().parent / "warpdesk_backend.log"


def log_line(message: str, style: str = "dim") -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    LOG_LINES.append((ts, message, style))


def run_cmd(command: List[str], timeout: int = 30) -> Tuple[int, str]:
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + "\n[TIMEOUT]"
        return 0, out
    except Exception as exc:
        return 1, str(exc)


def ensure_gst_path() -> None:
    candidates = [
        Path(r"C:\gstreamer\1.0\msvc_x86_64\bin"),
        Path(r"C:\Program Files\gstreamer\1.0\msvc_x86_64\bin"),
        Path(r"C:\Program Files\GStreamer\1.0\msvc_x86_64\bin"),
        Path.home() / "AppData" / "Local" / "Programs" / "gstreamer" / "1.0" / "msvc_x86_64" / "bin",
    ]

    for path in candidates:
        exe = path / "gst-launch-1.0.exe"
        if exe.exists():
            os.environ["PATH"] = str(path) + os.pathsep + os.environ.get("PATH", "")
            os.environ["GST_BIN_DIR"] = str(path)
            return


def gst_executable(name: str) -> str:
    gst_bin_dir = os.getenv("GST_BIN_DIR", "")
    if gst_bin_dir:
        full = Path(gst_bin_dir) / f"{name}.exe"
        if full.exists():
            return str(full)

    found = shutil.which(name)
    if found:
        return found

    return name


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def detect_os_label() -> str:
    try:
        return platform.platform(aliased=True, terse=True)
    except Exception:
        return os.name


def detect_gpu_label() -> str:
    if os.name != "nt":
        return "Unknown GPU"
    ps = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        "(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) -join '; '",
    ]
    rc, out = run_cmd(ps, timeout=6)
    if rc != 0:
        return "Unknown GPU"
    lines = [line.strip() for line in (out or "").splitlines() if line.strip()]
    if not lines:
        return "Unknown GPU"
    gpu = lines[0]
    return gpu or "Unknown GPU"


def render_header(os_label: str, gpu_label: str) -> Panel:
    lines = Text()
    lines.append("REMOTE", style="bold cyan")
    lines.append("\n")
    lines.append(f"Selkies-GStreamer // {gpu_label}", style="dim")
    return Panel(lines, border_style="bold cyan", title="[bold cyan]REMOTE[/]", subtitle=f"[dim]{os_label}[/]")


def render_system_table(host_ip: str, port: int, os_label: str, gpu_label: str, encoder: str, audio_src: str, bitrate_mbps: int) -> Table:
    tbl = Table(title="[bold cyan]System Info[/]", header_style="bold cyan")
    tbl.add_column("[dim]Property[/dim]")
    tbl.add_column("[bold white]Value[/bold white]")
    tbl.add_row("OS", f"[bold white]{os_label}[/]")
    tbl.add_row("GPU", f"[bold white]{gpu_label}[/]")
    tbl.add_row("Host IP", f"[bold white]{host_ip}[/]")
    tbl.add_row("Port", f"[bold white]{port}[/]")
    tbl.add_row("Encoder", encoder)
    tbl.add_row("Audio source", audio_src)
    tbl.add_row("Resolution", "[bold white]1920 × 1080[/]")
    tbl.add_row("Framerate", "[bold white]60 fps[/]")
    tbl.add_row("Bitrate", f"[bold white]{bitrate_mbps} Mbps[/]")
    return tbl


def verify_gstreamer_step(step_text: str, command: List[str], timeout: int = 30) -> Tuple[bool, str]:
    with Status(f"[dim]{step_text}[/]", console=console, spinner="dots"):
        rc, out = run_cmd(command, timeout=timeout)
    if rc == 0:
        console.print(f"[bold green]✓[/] {step_text}")
        return True, out
    console.print(f"[bold red]✗[/] {step_text} — {out.strip().splitlines()[-1] if out.strip() else 'failed'}")
    return False, out


def fetch_health(base_url: str, token: str) -> dict:
    req = Request(
        f"{base_url}/api/health",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    ctx = ssl._create_unverified_context()
    with urlopen(req, context=ctx, timeout=2) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def load_launcher_config() -> dict:
    default_cfg = {"username": "admin", "password": "warpdesk"}
    try:
        if LAUNCHER_CONFIG_PATH.exists():
            data = json.loads(LAUNCHER_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                username = str(data.get("username", "admin")).strip() or "admin"
                password = str(data.get("password", "warpdesk"))
                return {"username": username, "password": password}
    except Exception as exc:
        log_line(f"config load failed: {exc}", style="bold yellow")
    return default_cfg


def save_launcher_config(cfg: dict) -> None:
    LAUNCHER_CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def fetch_login_token(base_url: str, username: str, password: str) -> str:
    payload = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = Request(
        f"{base_url}/api/login",
        headers={"Content-Type": "application/json"},
        data=payload,
        method="POST",
    )
    ctx = ssl._create_unverified_context()
    with urlopen(req, context=ctx, timeout=3) as resp:
        body = json.loads(resp.read().decode("utf-8", errors="replace"))
    token = str(body.get("token", "")).strip()
    return token


def update_auth(base_url: str, token: str, username: str, password: str) -> dict:
    payload = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = Request(
        f"{base_url}/api/auth/update",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        data=payload,
        method="POST",
    )
    ctx = ssl._create_unverified_context()
    with urlopen(req, context=ctx, timeout=3) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def poll_action_key() -> Optional[str]:
    if os.name != "nt":
        return None
    try:
        import msvcrt
        if not msvcrt.kbhit():
            return None
        key = msvcrt.getwch()
        if key in {"\x00", "\xe0"}:
            # Ignore Windows extended key prefixes (arrows/function keys).
            _ = msvcrt.getwch()
            return None
        key = key.strip().lower()
        return key or None
    except Exception:
        return None


def prompt_update_credential(field: str, current_value: str, mask_input: bool = False) -> Optional[str]:
    prompt = f"Enter new {field} (ESC to cancel, ENTER to save): "
    console.print(f"\n[bold cyan]{prompt}[/]", end="")
    if os.name == "nt":
        try:
            import msvcrt
            chars: list[str] = []
            while True:
                ch = msvcrt.getwch()
                if ch == "\x1b":
                    console.print("")
                    return None
                if ch in {"\r", "\n"}:
                    console.print("")
                    break
                if ch == "\x03":
                    raise KeyboardInterrupt()
                if ch in {"\x08", "\x7f"}:
                    if chars:
                        chars.pop()
                        console.print("\b \b", end="")
                    continue
                if ch.isprintable() and len(chars) < 128:
                    chars.append(ch)
                    console.print("*" if mask_input else ch, end="")
            value = "".join(chars).strip()
        except Exception:
            value = ""
    else:
        value = console.input().strip()

    if not value:
        return None
    if value == current_value:
        return None
    return value


def graceful_stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=5)
        return
    except Exception:
        pass
    try:
        proc.terminate()
        proc.wait(timeout=5)
        return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def render_live_layout(started_at: float, connected_since: Optional[float], health: dict, encoder_name: str, bitrate_bps: int, username: str, password: str) -> Layout:
    now = time.time()
    session_duration = int(now - started_at)
    conn_duration = int(now - connected_since) if connected_since else 0

    top_tbl = Table(show_header=False)
    top_tbl.add_column("k", style="dim")
    top_tbl.add_column("v", style="bold white")
    client_ip = str(health.get("client_ip", "unknown") or "unknown")
    top_tbl.add_row("Client IP", client_ip)
    top_tbl.add_row("Session duration", f"{session_duration}s")
    top_tbl.add_row("Connected since", datetime.fromtimestamp(connected_since).strftime("%Y-%m-%d %H:%M:%S") if connected_since else "-")

    stats = Table(title="[bold cyan]Live Stats[/]", header_style="bold cyan")
    stats.add_column("[dim]Metric[/dim]")
    stats.add_column("[bold white]Value[/bold white]")
    stats.add_row("FPS", f"[bold white]{health.get('target_fps', 60)}[/]")
    stats.add_row("Encoder latency", "[bold white]n/a[/]")
    stats.add_row("Bitrate", f"[bold white]{max(1, bitrate_bps // 1000)} kbps[/]")
    stats.add_row("Audio", "[bold green]OK[/]" if health.get("audio_drops", 0) == 0 else "[bold yellow]degraded[/]")
    stats.add_row("Connection", "[bold green]LAN direct[/]")
    stats.add_row("Encoder", encoder_name)

    log_text = Text()
    for ts, msg, style in list(LOG_LINES):
        log_text.append(f"{ts} ", style="dim")
        log_text.append(msg + "\n", style=style)

    pwd_preview = "*" * max(4, min(16, len(password or "")))
    actions_text = (
        f"[bold]User:[/] {username}\n"
        f"[bold]Password:[/] {pwd_preview}\n"
        "[bold magenta][1][/] Edit Password    "
        "[bold magenta][2][/] Edit Username    "
        "[bold magenta][Q][/] Quit\n"
        "[dim]ESC cancels edit, ENTER saves[/]"
    )

    layout = Layout()
    layout.split_column(
        Layout(Panel(top_tbl, title="[bold cyan]Connection[/]", border_style="bold cyan"), size=7),
        Layout(stats),
        Layout(Panel(log_text, title="[bold cyan]Logs[/]", border_style="bold cyan", box=box.ROUNDED), size=8),
        Layout(Panel(actions_text, title="[bold cyan]Actions[/]", border_style="bold cyan"), size=6),
    )
    _ = conn_duration
    return layout


def main() -> int:
    os.environ["GST_SCHEDULING"] = "sync"
    os.environ.setdefault("GST_DEBUG", "0")
    ensure_gst_path()

    host_ip = get_local_ip()
    os_label = detect_os_label()
    gpu_label = detect_gpu_label()
    port = int(os.getenv("WARPDESK_PORT", "8443"))
    bitrate_bps = int(os.getenv("WARPDESK_VIDEO_MAX_BITRATE_BPS", "20000000"))

    console.print(render_header(os_label, gpu_label))

    gst_launch = gst_executable("gst-launch-1.0")
    gst_inspect = gst_executable("gst-inspect-1.0")

    ok, out = verify_gstreamer_step("Checking GStreamer installation...", [gst_launch, "--version"])
    if not ok:
        console.print(Panel("[bold red]GStreamer CLI not found. Install GStreamer 1.22+ and add bin to PATH.[/]", border_style="bold red", title="[bold red]Startup Error[/]"))
        return 1

    ok, _ = verify_gstreamer_step("Verifying encoder: mfh264enc...", [gst_inspect, "mfh264enc"])
    encoder = "mf"
    encoder_label = "mfh264enc [bold green]HW ✓[/]"
    if not ok:
        ok_qsv, _ = verify_gstreamer_step("Verifying encoder fallback: qsvh264enc...", [gst_inspect, "qsvh264enc"])
        if not ok_qsv:
            ok_sw, _ = verify_gstreamer_step("Verifying software fallback: x264enc...", [gst_inspect, "x264enc"])
            if not ok_sw:
                console.print(Panel("[bold red]No usable H.264 encoder found (mfh264enc/qsvh264enc/x264enc).[/]", border_style="bold red", title="[bold red]Startup Error[/]"))
                return 1
            encoder = "sw"
            encoder_label = "x264enc [bold red]Software Encoded (GPU not supported)[/]"
        else:
            encoder = "qsv"
            encoder_label = "qsvh264enc [bold green]HW ✓[/]"

    ok, _ = verify_gstreamer_step("Verifying audio: wasapi2src loopback...", [gst_inspect, "wasapi2src"])
    audio_label = "wasapi2src loopback [bold green]✓[/]"
    if not ok:
        ok_wasapi, _ = verify_gstreamer_step("Verifying audio fallback: wasapisrc loopback...", [gst_inspect, "wasapisrc"])
        if not ok_wasapi:
            console.print(Panel("[bold red]No WASAPI source plugin found (wasapi2src/wasapisrc).[/]", border_style="bold red", title="[bold red]Startup Error[/]"))
            return 1
        audio_label = "wasapisrc loopback [bold yellow]fallback[/]"

    test1 = [
        gst_launch, "-v",
        "d3d11screencapturesrc", "num-buffers=120", "!",
        "video/x-raw(memory:D3D11Memory),framerate=60/1,width=1920,height=1080", "!",
        "d3d11colorconvert", "!",
        "d3d11convert", "!",
        "video/x-raw(memory:D3D11Memory),format=NV12", "!",
        "mfh264enc", "bitrate=20000", "low-latency=true", "!",
        "fakesink", "sync=false",
    ]
    if encoder == "qsv":
        test1 = [
            gst_launch, "-v",
            "d3d11screencapturesrc", "num-buffers=120", "!",
            "video/x-raw(memory:D3D11Memory),framerate=60/1,width=1920,height=1080", "!",
            "d3d11colorconvert", "!",
            "d3d11convert", "!",
            "video/x-raw(memory:D3D11Memory),format=NV12", "!",
            "qsvh264enc", "bitrate=20000", "low-latency=true", "!",
            "fakesink", "sync=false",
        ]
    if encoder == "sw":
        test1 = [
            gst_launch, "-v",
            "d3d11screencapturesrc", "num-buffers=120", "!",
            "videoconvert", "!",
            "x264enc", "tune=zerolatency", "speed-preset=ultrafast", "bitrate=12000", "key-int-max=30", "!",
            "fakesink", "sync=false",
        ]

    ok, out1 = verify_gstreamer_step("Building video pipeline...", test1, timeout=20)
    if not ok:
        console.print(Panel("[bold red]Video pipeline validation failed. See test output section.[/]", border_style="bold red", title="[bold red]Startup Error[/]"))
        console.print(Panel(out1[-4000:], title="[bold red]Test 1 Output[/]", border_style="bold red"))
        return 1

    test2 = [
        gst_launch, "-v",
        "wasapi2src", "loopback=true", "low-latency=true", "num-buffers=240", "!",
        "audio/x-raw,rate=48000,channels=2", "!",
        "audioconvert", "!", "audioresample", "!",
        "opusenc", "bitrate=128000", "!",
        "fakesink", "sync=false",
    ]
    ok2, out2 = verify_gstreamer_step("Building audio pipeline...", test2, timeout=12)
    if not ok2:
        test2_fb = [
            gst_launch, "-v",
            "wasapisrc", "loopback=true", "low-latency=true", "num-buffers=240", "!",
            "audio/x-raw,rate=48000,channels=2", "!",
            "audioconvert", "!", "audioresample", "!",
            "opusenc", "bitrate=128000", "!",
            "fakesink", "sync=false",
        ]
        ok2, out2 = verify_gstreamer_step("Building audio pipeline (fallback)...", test2_fb, timeout=12)
        if not ok2:
            console.print(Panel("[bold red]Audio loopback validation failed.[/]", border_style="bold red", title="[bold red]Startup Error[/]"))
            console.print(Panel(out2[-4000:], title="[bold red]Test 2 Output[/]", border_style="bold red"))
            return 1

    enc_probe = "mfh264enc"
    if encoder == "qsv":
        enc_probe = "qsvh264enc"
    if encoder == "sw":
        enc_probe = "x264enc"
    ok3, out3 = verify_gstreamer_step("Verifying encoder inspect output...", [gst_inspect, enc_probe], timeout=10)

    console.print(render_system_table(host_ip, port, os_label, gpu_label, encoder_label, audio_label, bitrate_bps // 1_000_000))

    launcher_cfg = load_launcher_config()
    os.environ["WARPDESK_USER"] = launcher_cfg["username"]
    os.environ["WARPDESK_PASSWORD"] = launcher_cfg["password"]

    os.environ["WEBRTC_ENCODER"] = encoder
    os.environ["WARPDESK_CODEC"] = "h264"
    os.environ["WARPDESK_AUDIO_SOURCE"] = "system"
    os.environ["WARPDESK_ALLOW_MIC_FALLBACK"] = "0"
    os.environ["WARPDESK_TUI"] = "0"

    console.print("[bold green]✓[/] Starting signaling server...")
    app_path = Path(__file__).resolve().parent / "app.py"
    proc = subprocess.Popen(
        [sys.executable, "-u", str(app_path)],
        cwd=str(app_path.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env={**os.environ.copy(), "PYTHONUNBUFFERED": "1"},
    )

    try:
        BACKEND_LOG_PATH.write_text("", encoding="utf-8")
    except Exception:
        pass
    backend_log_file = None
    try:
        backend_log_file = BACKEND_LOG_PATH.open("a", encoding="utf-8", errors="replace")
    except Exception:
        backend_log_file = None

    def pump_logs() -> None:
        if proc.stdout is None:
            return
        for line in proc.stdout:
            msg = line.strip()
            if not msg:
                continue
            style = "dim"
            low = msg.lower()
            if "warn" in low:
                style = "bold yellow"
            if "error" in low or "traceback" in low:
                style = "bold red"
            log_line(msg, style=style)
            if backend_log_file is not None:
                try:
                    backend_log_file.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
                    backend_log_file.flush()
                except Exception:
                    pass

    t = threading.Thread(target=pump_logs, daemon=True)
    t.start()

    console.print(f"[bold green]✓[/] Ready — waiting for connections on [bold white]{host_ip}:{port}[/]")

    started_at = time.time()
    connected_since = None
    base_url = f"https://127.0.0.1:{port}"
    token = ""
    health = {"target_fps": 60, "audio_drops": 0, "client_ip": "unknown", "connected_since": None}
    plain_mode = os.getenv("WARPDESK_LAUNCHER_PLAIN", "1") == "1"

    def ensure_health_token() -> str:
        nonlocal token
        if token:
            return token
        try:
            token = fetch_login_token(base_url, launcher_cfg["username"], launcher_cfg["password"])
            if token:
                log_line("health auth token acquired", style="dim")
        except Exception as exc:
            log_line(f"login for health failed: {exc}", style="bold yellow")
        return token

    def apply_credential_change(field: str, mask: bool = False) -> None:
        nonlocal token
        key = "password" if field == "password" else "username"
        current = launcher_cfg[key]
        new_value = prompt_update_credential(field, current, mask_input=mask)
        if not new_value:
            log_line(f"{field} update cancelled", style="dim")
            return

        # Keep old values until backend confirms auth update.
        old_username = launcher_cfg["username"]
        old_password = launcher_cfg["password"]
        launcher_cfg[key] = new_value

        current_token = token
        if not current_token:
            current_token = ensure_health_token()
        if not current_token:
            launcher_cfg["username"] = old_username
            launcher_cfg["password"] = old_password
            log_line(f"{field} update failed: auth token unavailable", style="bold red")
            return

        try:
            reply = update_auth(base_url, current_token, launcher_cfg["username"], launcher_cfg["password"])
            if not reply.get("success"):
                raise RuntimeError(str(reply.get("error", "unknown_error")))
        except Exception as exc:
            launcher_cfg["username"] = old_username
            launcher_cfg["password"] = old_password
            log_line(f"{field} update failed: {exc}", style="bold red")
            return

        save_launcher_config(launcher_cfg)
        os.environ["WARPDESK_USER"] = launcher_cfg["username"]
        os.environ["WARPDESK_PASSWORD"] = launcher_cfg["password"]
        token = ""
        log_line(f"{field} updated; backend + config synced", style="bold green")

    def handle_action_key(key: Optional[str]) -> bool:
        if key is None:
            return False
        if key == "1":
            apply_credential_change("password", mask=True)
            return False
        if key == "2":
            apply_credential_change("username", mask=False)
            return False
        if key in {"q", "Q"}:
            log_line("quit requested", style="bold yellow")
            graceful_stop_process(proc)
            console.print("[bold yellow]Exiting launcher...[/]")
            raise SystemExit(0)
        return False

    if plain_mode:
        console.print("[bold cyan]Launcher plain mode enabled (WARPDESK_LAUNCHER_PLAIN=1).[/]")
        try:
            last_health_poll = 0.0
            printed_log_count = 0
            while proc.poll() is None:
                now = time.time()
                if now - last_health_poll >= 1.0:
                    try:
                        ensure_health_token()
                        if token:
                            health = fetch_health(base_url, token)
                    except URLError:
                        pass
                    except Exception as exc:
                        log_line(f"health poll failed: {exc}", style="bold yellow")
                    last_health_poll = now
                if connected_since is None and health.get("connected_since"):
                    connected_since = float(health.get("connected_since"))
                if connected_since is None and int(health.get("active_peers", 0)) > 0:
                    connected_since = time.time()
                if int(health.get("active_peers", 0)) <= 0 and not health.get("connected_since"):
                    connected_since = None

                snap_logs = list(LOG_LINES)
                if printed_log_count > len(snap_logs):
                    printed_log_count = 0
                if printed_log_count < len(snap_logs):
                    for ts, msg, style in snap_logs[printed_log_count:]:
                        console.print(f"[dim]{ts}[/] {msg}", style=style)
                    printed_log_count = len(snap_logs)

                key = poll_action_key()
                if key:
                    handle_action_key(key)
                time.sleep(0.1)
        except KeyboardInterrupt:
            console.print("[bold yellow]Shutting down...[/]")
            graceful_stop_process(proc)
            return 0
    else:
        with Live(render_live_layout(started_at, connected_since, health, encoder_label, bitrate_bps, launcher_cfg["username"], launcher_cfg["password"]), refresh_per_second=2, console=console, auto_refresh=False) as live:
            try:
                last_health_poll = 0.0
                while proc.poll() is None:
                    now = time.time()
                    if now - last_health_poll >= 1.0:
                        try:
                            ensure_health_token()
                            if token:
                                health = fetch_health(base_url, token)
                        except URLError:
                            pass
                        except Exception as exc:
                            log_line(f"health poll failed: {exc}", style="bold yellow")
                        last_health_poll = now

                    if connected_since is None and health.get("connected_since"):
                        connected_since = float(health.get("connected_since"))
                    if connected_since is None and int(health.get("active_peers", 0)) > 0:
                        connected_since = time.time()
                    if int(health.get("active_peers", 0)) <= 0 and not health.get("connected_since"):
                        connected_since = None

                    key = poll_action_key()
                    if key:
                        handle_action_key(key)

                    live.update(render_live_layout(started_at, connected_since, health, encoder_label, bitrate_bps, launcher_cfg["username"], launcher_cfg["password"]))
                    time.sleep(0.1)
            except KeyboardInterrupt:
                console.print("[bold yellow]Shutting down...[/]")
                graceful_stop_process(proc)
                return 0

    rc = proc.returncode or 0
    if rc != 0:
        console.print(Panel(f"[bold red]Pipeline/server exited with code {rc}[/]", title="[bold red]Runtime Error[/]", border_style="bold red"))
        if rc in {-1, 4294967295}:
            console.print(
                Panel(
                    "[bold yellow]Backend terminated abnormally (Windows -1 / 4294967295).[/]\n"
                    "This commonly indicates external process termination, abrupt interpreter abort, or a native plugin crash.\n"
                    "See the Server Output panel below for the final emitted lines.",
                    title="[bold yellow]Exit Code Hint[/]",
                    border_style="bold yellow",
                )
            )
        recent_logs = "\n".join([f"{ts} {msg}" for ts, msg, _style in list(LOG_LINES)])
        if recent_logs:
            console.print(Panel(recent_logs, title="[bold red]Server Output[/]", border_style="bold red"))

    console.print(Panel(out1[-2000:], title="[bold cyan]Test 1 Output[/]", border_style="bold cyan", box=box.ROUNDED))
    console.print(Panel(out2[-2000:], title="[bold cyan]Test 2 Output[/]", border_style="bold cyan", box=box.ROUNDED))
    console.print(Panel("\n".join((out3 or "").splitlines()[:5]), title="[bold cyan]Test 3 Output[/]", border_style="bold cyan", box=box.ROUNDED))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
