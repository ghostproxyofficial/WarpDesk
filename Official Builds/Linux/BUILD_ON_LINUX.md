# Build WarpDesk on Linux

From repository root:

```bash
cd agent/tauri-control
npm install
npm run tauri build
```

Expected outputs may include:

- src-tauri/target/release/bundle/deb/*.deb
- src-tauri/target/release/bundle/rpm/*.rpm
- src-tauri/target/release/bundle/appimage/*.AppImage

Copy artifacts into this folder.
