# WarpDesk

WarpDesk is a self-hosted remote desktop stack:

- Python host agent (`agent/app.py`)
- Browser remote client (`web/`)
- Tauri desktop control app (`agent/tauri-control`)

## Repo Layout

```text
WarpDesk/
    agent/
        app.py
        tauri-control/
    web/
    Official Builds/
    start_all.bat
    start_all.sh
```

## Local Development

Start backend + web launcher:

```bash
# Windows
start_all.bat

# Linux/macOS
./start_all.sh
```

Then open:

- Web UI: `http://localhost:8080`
- Host API: `https://localhost:8443`

## Remote Access (Cloudflare Tunnel + TURN)

For outside-LAN access without opening home router ports:

- Use Cloudflare Tunnel for the WarpDesk backend URL (signaling/API).
- Use TURN for WebRTC relay.

Start with `agent/.env.example`:

1. Copy it to `agent/.env.local`.
2. Fill in your Cloudflare TURN token ID and API token.
3. Start WarpDesk and use your Cloudflare Tunnel URL in the login `Connection URL` field.

Detailed backend variables and explanations are in `agent/README.md`.

## Tauri Control App

From `agent/tauri-control`:

```bash
npm install
npm run tauri dev
```

Release build:

```bash
npm run tauri build
```

## Official Builds

Release artifacts are collected under `Official Builds/` with per-platform folders:

- `Official Builds/Windows/`
- `Official Builds/macOS/`
- `Official Builds/Linux/`

Notes:

- Windows installers can be produced on Windows.
- macOS bundles must be built on macOS.
- Linux bundles should be built on Linux (or a dedicated cross-build environment).

## License

MIT
