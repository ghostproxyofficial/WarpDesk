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

GPL v3.0
