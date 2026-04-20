# WarpDesk Beta
Windows should work, MacOS and Linux may glitch.
The project is in Early Beta, expect bugs

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

### Cloudflare Tunnel Setup (Battle-Tested)

This is the setup flow that worked reliably during real debugging.

1. Start WarpDesk locally first:

```bat
start_all.bat
```

2. Confirm local services:

- Backend should answer at `https://localhost:8443/api/health`.
- Web static client is at `http://localhost:8080`.

3. Use a **named tunnel** (recommended), not quick tunnel mode.

Create/route once:

```powershell
cloudflared tunnel create warpdesk
cloudflared tunnel route dns warpdesk your-hostname.yourdomain.com
```

4. Create `C:\Users\<you>\.cloudflared\config.yml` with ingress to the backend:

```yaml
tunnel: <your-tunnel-id>
credentials-file: C:/Users/<you>/.cloudflared/<your-tunnel-id>.json

ingress:
    - service: https://localhost:8443
        originRequest:
            noTLSVerify: true
```

5. Validate and run tunnel:

```powershell
cloudflared tunnel ingress validate
cloudflared tunnel run warpdesk
```

6. In WarpDesk login, set Connection URL to:

`https://your-hostname.yourdomain.com`

### Important Gotchas We Hit

- `cloudflared tunnel --url ...` is quick-tunnel mode and can show:
    `No ingress rules were defined...`
    This is expected if you intended to run a named tunnel config.
- Named tunnel command should be:
    `cloudflared tunnel run warpdesk`
- If Cloudflare returns `502` and logs mention `x509: certificate signed by unknown authority`, your origin is using a local/self-signed cert and needs `noTLSVerify: true` in tunnel origin settings.
- `config.yml` must be valid YAML. If you accidentally paste JSON credentials into `config.yml`, ingress parsing fails.
- The file `<tunnel-id>.json` in `.cloudflared` is credentials only. Do not edit it as tunnel config.

### Quick Smoke Test

With WarpDesk and tunnel both running:

```powershell
curl.exe -k https://localhost:8443/api/health
curl.exe https://your-hostname.yourdomain.com/api/health
```

Both should return success (`200`) before testing remote desktop.

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
