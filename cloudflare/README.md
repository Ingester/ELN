# ELN Cloudflare Tunnel Setup

This setup exposes only the native FastAPI HTML interface on `127.0.0.1:8000`.
Do not expose the Flet desktop/web shell on `8550`.

There are two valid Cloudflare modes:

- Recommended: Cloudflare Dashboard remote-managed tunnel. Cloudflare shows a
  `cloudflared service install ...` command and manages the route online.
- Advanced: local `cloudflare/config.yml`. Use this only if you want local
  config files.

## 1. Install cloudflared

Open PowerShell:

```powershell
winget install --id Cloudflare.cloudflared
```

## 2. Create the tunnel in Cloudflare Dashboard

1. Open `https://one.dash.cloudflare.com`.
2. Go to `Networks` -> `Connectors` -> `Cloudflare Tunnels`.
3. Select `Create a tunnel`.
4. Choose `Cloudflared`.
5. Name it, for example `eln-app`.
6. Run the Windows connector command shown by Cloudflare, for example:

```powershell
cloudflared.exe service install <TOKEN_FROM_CLOUDFLARE>
```

When the connector becomes healthy, continue to the public hostname step.

## 3. Add the public hostname

In the tunnel page, add a public hostname:

- Subdomain: `eln` or another name you want.
- Domain: your Cloudflare-managed domain.
- Type: `HTTP`.
- URL: `localhost:8000`.
- Path: leave empty.

The public URL will look like:

```text
https://eln.yourdomain.com
```

Visit the ELN runner at:

```text
https://eln.yourdomain.com/run
```

## 4. Optional local config.yml mode

Skip this section if you used the Cloudflare Dashboard service install command.

If you want local config instead, copy the template:

```powershell
Copy-Item .\cloudflare\config.example.yml .\cloudflare\config.yml
```

Edit `cloudflare\config.yml`:

```yaml
tunnel: YOUR_TUNNEL_ID
credentials-file: C:\Users\YOUR_WINDOWS_USER\.cloudflared\YOUR_TUNNEL_ID.json

ingress:
  - hostname: eln.yourdomain.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

`config.yml` is ignored by git because it contains local/private tunnel details.

## 5. Set the ELN app password

Before public access, set an ELN password:

```powershell
setx ELN_AUTH_PASSWORD "replace-with-a-long-password"
```

Close and reopen PowerShell after `setx`, or set it for the current terminal:

```powershell
$env:ELN_AUTH_PASSWORD = "replace-with-a-long-password"
```

Optional but recommended:

```powershell
setx ELN_AUTH_COOKIE_SECRET "replace-with-a-different-long-random-secret"
```

## 6. Start ELN in tunnel mode

From this app folder:

```powershell
.\start_eln_cloudflare.ps1
```

Or double-click:

```text
start_eln_cloudflare.bat
```

This starts:

- ELN API/native HTML on `127.0.0.1:8000`.
- Flet local shell on `127.0.0.1:8550`, kept local only.
- If `cloudflare\config.yml` exists: local `cloudflared tunnel --config ... run`.
- If no `config.yml` exists: assumes the Cloudflare Dashboard Windows service is
  already running.

## 7. Recommended extra protection

Use Cloudflare Access in front of the hostname so only your email can open it.
Keep `ELN_AUTH_PASSWORD` enabled even with Cloudflare Access; it is a second
layer in case the Cloudflare policy is changed later.
