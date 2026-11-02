# Champ Studio — Team Server Deployment

One container serves the whole team: web UI + API on port 8787. Works on any
Docker host (Hetzner/DO droplet, EC2, Railway, a Mac mini in the office).

## Quick start (any Linux server with Docker)

```bash
git clone <repo> champ-studio && cd champ-studio
cp .env.example .env
# Edit .env:
#   OPENROUTER_API_KEY=sk-or-v1-...          ← team key (set a spend limit!)
#   CHAMP_STUDIO_ACCESS_TOKEN=<long-random>  ← REQUIRED on a shared server
docker compose up -d --build
```

Team members open `http://<server>:8787`. The image is multi-arch
(linux/amd64 + arm64), so it runs on Intel servers and ARM (Graviton/Apple) alike.

## Access control

`CHAMP_STUDIO_ACCESS_TOKEN` gates every API call (Bearer token). The web UI will
prompt 401s without it; teammates put the token into the Mac app's Settings
("Access token") or use the web UI from behind your VPN. For anything
internet-facing, put a reverse proxy with TLS in front (Caddy):

```
studio.example.com {
    reverse_proxy localhost:8787
}
```

## Keys strategy (per Celsus conventions)

- One **team OpenRouter key with a low spend limit** in `.env` (the group pattern:
  shared keys funded $10–$100 with hard caps at openrouter.ai/settings/keys), or
- Leave `.env` keys empty and let each user set their own key in Settings — note
  that on a shared server, Settings keys are **shared by everyone** on that server.

## Data

Assets + settings live in the `champ-data` volume. Backup:
`docker run --rm -v champ-data:/data -v $PWD:/backup alpine tar czf /backup/champ-data.tgz /data`

## Upgrade

```bash
git pull && docker compose up -d --build
```


## Team mode: single sign-on, roles, budgets

Set these in `.env` (all optional; without them the server uses the shared access token above):

| Variable | Purpose |
|---|---|
| `CHAMP_OIDC_ISSUER` | Your identity provider, e.g. `https://login.microsoftonline.com/<tenant>/v2.0` or `https://accounts.google.com` |
| `CHAMP_OIDC_CLIENT_ID` / `CHAMP_OIDC_CLIENT_SECRET` | App registration; redirect URI `<CHAMP_PUBLIC_URL>/api/auth/callback` |
| `CHAMP_PUBLIC_URL` | e.g. `https://studio.example.com` (HTTPS makes session cookies `Secure`) |
| `CHAMP_ADMIN_EMAILS` | Comma-separated; these become admins on first login |
| `CHAMP_DEFAULT_ROLE` | Role for everyone else: `viewer`, `reviewer`, `editor` (default) or `admin` |
| `CHAMP_ALLOWED_DOMAINS` | Optional e-mail domain allowlist, e.g. `championsmail.com` |
| `CHAMP_RENDER_WORKERS` | Renders allowed at once (default 2); the rest queue |
| `CHAMP_MAX_UPLOAD_MB` | Upload limit (default 4096) |

Roles: **viewer** (watch), **reviewer** (+ comment, approve), **editor** (+ create and edit),
**admin** (+ users, brand-kit deletion, audit log). Admins change roles in Settings → Team.
Every create/save/render/approve/login is written to the audit log.

Budgets: Settings → Usage & budget sets a monthly cap and a per-project cap. Every paid call is
estimated first and refused (HTTP 402) when it would cross a cap; actual costs land in the ledger.

Review links (`/review.html?t=…`) let clients watch a render, comment at exact frames and approve or
request changes without an account. Treat the link like a password; delete the project to revoke.

Storage and database: one SQLite database (WAL) plus files in the `champ-data` volume. That is
plenty for a marketing team on one server; back up the volume (`docker run --rm -v
champ_studio_champ-data:/data -v $PWD:/b alpine tar czf /b/champ-backup.tgz /data`). A Postgres +
S3/MinIO profile is on the roadmap for multi-server deployments.
