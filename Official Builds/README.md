# Official Builds

This folder stores curated release artifacts for distribution.

## Layout

- Windows/
- macOS/
- Linux/

## Current Release

Version: 0.1.1

### Windows

Included:

- WarpDesk_0.1.1_x64_en-US.msi
- WarpDesk_0.1.1_x64-setup.exe
- WarpDesk_0.1.1_x64_portable.exe

### macOS

Build on macOS host from `agent/tauri-control`:

```bash
npm install
npm run tauri build
```

Copy resulting `.dmg`/`.app` artifacts into `Official Builds/macOS/`.

### Linux

Build on Linux host from `agent/tauri-control`:

```bash
npm install
npm run tauri build
```

Copy resulting `.deb`/`.rpm`/`.AppImage` artifacts into `Official Builds/Linux/`.
