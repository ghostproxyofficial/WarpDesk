# WarpDesk Tauri Control

Desktop control panel for WarpDesk using:

- Tauri v2 (`src-tauri/`)
- Vanilla frontend (`index.html`, `src/main.js`, `src/styles.css`)

## What it does

- Starts/stops the Python host backend
- Updates host/runtime settings (auth, port, FPS, scale, monitor)
- Checks host port reachability
- Connects to remote host via web login pipeline and opens remote session in a new Tauri window

## Development

From `agent/tauri-control`:

```bash
npm install
npm run tauri dev
```

## Release Build

```bash
npm run tauri build
```

Expected outputs by platform:

- Windows: MSI/NSIS installers
- macOS: `.dmg`/`.app` bundles (native macOS build host required)
- Linux: `.deb`/`.rpm`/`.AppImage` (native Linux build host/toolchain required)

## Packaging Notes

- Build resources include `agent/app.py`, the `web/` client, and `agent/.venv` for packaged runtime startup.
- Runtime path resolution in Rust supports dev layout and packaged `_up_`/resource layouts.
