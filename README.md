# Champ Studio

**Self-hosted AI creative studio by Champions Group** — the evolution of Champ Video.
Create images, animate them into videos, and derive designer assets — true **SVG vectors,
skeletons, line art, transparent cutouts, palettes, halftones** — from any generation.
Bring your own keys; everything runs on your machine.

- **Providers:** [OpenRouter](https://openrouter.ai) (one key → all image models via the
  unified `/api/v1/images` API **and** all video models — Sora 2, Veo 3.1, Kling, Seedance,
  Wan — via the async `/api/v1/videos` API), plus optional **MuAPI** as a second engine.
- **Derivative Lab runs 100% locally** (vtracer, U²-Net, OpenCV) — no API cost, no upload.
- Every asset keeps its **lineage**: image → video → vector chains are traceable.

## Install — Mac (native)

```bash
git clone <this repo> && cd "Champ Studio"
./scripts/install-mac.sh          # engine + Champ Studio.app
./scripts/run-local.sh            # engine + web UI on http://127.0.0.1:8787
open "macapp/Champ Studio.app"        # native app (talks to the same engine)
```

Add your OpenRouter key in **Settings** (either UI). Keys are stored `0600` in
`~/.champstudio/settings.json` (Mac app also keeps them in your Keychain), and never
leave your machine except to the provider.

## Install — Windows / Linux / cloud (Docker, identical behavior)

```bash
cp .env.example .env              # optional: put keys here
docker compose up -d --build
# → http://localhost:8787
```

Same engine, same web UI, same API — the container is the parity guarantee across
platforms. For a team deployment, set `CHAMP_STUDIO_ACCESS_TOKEN` in `.env` and share
`http://<host>:8787` + the token; teammates can also point the Mac app's engine URL at it.

## What's inside

| Path | What |
|---|---|
| `engine/` | FastAPI engine — providers, transforms, asset library (pytest suite) |
| `web/` | Buildless web UI (Champ Suite design language), served by the engine |
| `macapp/` | Native SwiftUI macOS app (SPM, no Xcode needed) |
| `docker/` + `docker-compose.yml` | Container packaging |
| `docs/superpowers/specs/` | Design spec |

## API in 20 seconds

`GET /api/health` · `GET/PUT /api/settings` · `GET /api/models/{images,videos}` ·
`POST /api/images` (sync) · `POST /api/videos` → job → `GET /api/videos/jobs/{id}` ·
`GET /api/transform` + `POST /api/transform/{tool}` · `GET /api/assets` (+upload/file/delete,
lineage). Everything the two UIs do goes through this one API — build your own client if
you want.

## Derivative Lab tools

`vectorize` (color/binary SVG) · `skeleton` (1px structural spine, PNG+SVG) · `lineart` ·
`remove-bg` (local U²-Net) · `palette` (swatch PNG + CSS vars + JSON) · `halftone` ·
`posterize` · `upscale` (2×/4×). Outputs are assets too — chain them.

## Development

```bash
cd engine && uv venv --python-preference only-managed -p 3.12 .venv
uv pip install -p .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest -m "not slow"     # 19 tests
cd ../macapp && swift build                   # Mac app
```
