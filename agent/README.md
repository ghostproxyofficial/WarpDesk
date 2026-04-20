# WarpDesk Agent

Python backend service for WarpDesk host streaming and control.

## API Endpoints

- `POST /api/login`
- `GET /api/session`
- `GET /api/device-info`
- `POST /api/auth/update`
- `POST /api/webrtc/offer`
- `GET /api/health`

## Run From Repo Root

Windows:

```bat
start_all.bat
```

Linux/macOS:

```bash
./start_all.sh
```

Default values:

- Web UI: `http://localhost:8080`
- Connection URL: `https://localhost:8443`
- Username: `admin`
- Password: `warpdesk`

## Runtime Notes

- A local virtual environment is created in `agent/.venv` on first run.
- If certificate files exist, HTTPS is used; otherwise the agent falls back based on launcher behavior.
- `selkies_gst_presets.py` stores media pipeline presets used by launcher/runtime tuning.

## Control App

Desktop control app lives in `agent/tauri-control`.

- Dev: `npm run tauri dev`
- Build: `npm run tauri build`
