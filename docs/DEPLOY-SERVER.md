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
