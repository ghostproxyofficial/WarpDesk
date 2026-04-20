# WarpDesk Agent

Python backend service for WarpDesk host streaming and control.

## API Endpoints

- `POST /api/login`
- `GET /api/session`
- `GET /api/device-info`
- `GET /api/ice-servers`
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

## Cloudflare Tunnel + TURN

Use a Cloudflare Tunnel hostname as the connection URL in the client (for example, `https://desk.example.com`).

- Signaling/API (`/api/*` and `/ws`) goes through Cloudflare Tunnel.
- WebRTC media can relay through TURN when direct ICE paths fail.

### Simple Mental Model

- `Connection URL` in WarpDesk login = your Cloudflare Tunnel URL.
- `TURN` is not the login URL. TURN is only used behind the scenes for media relay.
- WarpDesk backend now requests short-lived TURN credentials from Cloudflare and sends them to the browser via `GET /api/ice-servers`.

### Local Env File Support

The backend loads env files automatically from these locations (first one wins if a value is already set):

- repo root: `.env`
- repo root: `.env.local`
- `agent/.env`
- `agent/.env.local`

This is useful for local development and avoids hardcoding secrets in code.

Environment variables:

- `WARPDESK_TURN_URLS` comma-separated TURN URLs for browser ICE config.
	Example: `turn:turn.example.com:3478?transport=udp,turns:turn.example.com:5349?transport=tcp`
- `WARPDESK_TURN_USERNAME` TURN username.
- `WARPDESK_TURN_CREDENTIAL` TURN credential/password.
- `WARPDESK_ICE_SERVERS_JSON` optional JSON override for full ICE config array.
	If set, this overrides `WARPDESK_TURN_*` and default STUN entries.
- `WARPDESK_GST_STUN_SERVER` optional host-side STUN for GStreamer (`stun://...`).
- `WARPDESK_GST_TURN_SERVER` optional host-side TURN for GStreamer (`turn://user:pass@host:port`).

Cloudflare short-lived TURN variables:

- `CLOUDFLARE_TURN` set `true` to enable Cloudflare TURN credential minting, `false` to disable.
- `WARPDESK_CF_TURN_TOKEN_ID` Cloudflare TURN Token ID.
- `WARPDESK_CF_TURN_API_TOKEN` Cloudflare API token used to mint temporary credentials.
- `WARPDESK_CF_TURN_TTL_SECONDS` credential TTL in seconds (default `3600`).
- `WARPDESK_CF_TURN_API_BASE` API base URL (default `https://rtc.live.cloudflare.com/v1`).
- `WARPDESK_CF_TURN_TIMEOUT_SECONDS` HTTP timeout for Cloudflare API call (default `8`).

When Cloudflare TURN variables are not set, WarpDesk falls back to static ICE config and default public STUN servers.

### Setup Steps (Beginner)

1. Create `agent/.env.local`.
2. Put your values in it:

```env
CLOUDFLARE_TURN=true
WARPDESK_CF_TURN_TOKEN_ID=your_turn_token_id
WARPDESK_CF_TURN_API_TOKEN=your_api_token
WARPDESK_CF_TURN_TTL_SECONDS=3600
```

3. Start WarpDesk.
4. In the web login, enter your Cloudflare Tunnel URL as `Connection URL`.
5. Login with WarpDesk username/password.

### Security Notes

- Never commit `.env` or `.env.local` with secrets.
- If a token was shared publicly, rotate/revoke it in Cloudflare and issue a new one.

## Control App

Desktop control app lives in `agent/tauri-control`.

- Dev: `npm run tauri dev`
- Build: `npm run tauri build`
