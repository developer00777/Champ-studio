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
- **Voice** — paste a script, pick a model and voice, get an mp3 in the Library (`POST /api/audio`, OpenRouter `/audio/speech`).
- **Lip Sync** — a portrait plus a voice track becomes a talking-head video through `heygen/avatar-iv` on OpenRouter (`POST /api/lipsync`). No HeyGen account needed; billed per second on the OpenRouter key.

### Video studio (2026-10 roadmap — see `docs/superpowers/plans/2026-10-30-studio-roadmap.md`)

- **Director** — a brief becomes a finished video: LLM script and scene plan → storyboard stills you
  approve → voiceover, AI clips (or stills with motion, text cards, avatar presenter), music, word-level
  captions → one render per aspect ratio (16:9, 9:16, 1:1, 4:5). Edit by chat ("make the hook
  punchier") — only the affected scenes are re-made. Cost estimate before you pay; budget caps.
- **Templates** — 50+ original B2B templates (ads, product promo, explainers, data/thought leadership,
  social proof, events, recruitment, sales enablement, brand utilities, company news) in every ratio,
  driven by brand kits. Fill by hand or from a brief (AI), batch-render personalised videos from a CSV,
  insert titles/lower thirds into the editor, save any sequence as a team template.
- **Editor** (`/editor/`) — Premiere-style multi-track timeline: source/program monitors, razor, ripple,
  roll, J/L cuts, snapping, markers, keyframes, transitions, titles, colour, audio mixing, JKL, undo,
  autosave + versions, edit locks, exports (H.264, ProRes, DNxHR, WebM, review copies), SRT/VTT,
  Premiere XML / EDL / OTIO interchange.
- **Motion graphics** (Essential Graphics) — graphic clips that hold a stack of text, shape (rect,
  ellipse, line, triangle, polygon, star, angled gradients) and image layers, each with its own
  keyframes and in/out animations. Per-character / word / line text animators, number count-ups,
  responsive pins (a bar that grows with the name typed into it), masks, and exposed "master
  properties" so editors change a graphic's fields without opening its layers. Browse 30+
  motion-graphics templates (lower thirds, titles, callouts, stats and charts, CTAs, end cards,
  transitions) with hover previews; select and drag layers directly in the Program monitor; group
  clips into a graphic, ungroup, or save any graphic as a team template (like a .mogrt).
- **AI editing** — text-based editing, silence and filler removal (captions re-timed correctly),
  auto captions, auto ducking, auto reframe to vertical, scene detection, speech enhancement,
  B-roll, chat commands, AI rough cut from raw footage.
- **Team** — brand kits, frame-accurate review links with approve / request changes, comments as
  markers, OIDC single sign-on with viewer/reviewer/editor/admin roles, audit log, cost ledger.
- **Cheap models by default** (Settings → AI models): Claude Haiku 5.5 for scripts, DeepSeek V4.1 Flash
  for bulk text, Veo 3.1 Lite for drafts, Veo 3.1 Fast for finals, Gemini Flash Lite Image for stills,
  Lyria 3 clips for music — each with an automatic fallback.

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

## Install — Windows desktop app (one click, Docker underneath)

Double-click `desktop\windows\Install Champ Studio.cmd`. It adds **Champ Studio** to the Desktop
and Start menu. The first launch installs Docker Desktop if needed, builds the engine
(5–10 minutes) and opens Champ Studio in its own window; after that it opens in seconds. Teammates
get a zip from `desktop\windows\make-release.ps1`. Details:
[desktop/windows/README.md](desktop/windows/README.md).

## Install — Windows / Linux / cloud (Docker, identical behavior)

```bash
cp .env.example .env              # optional: put keys here
docker compose up -d --build
# → http://localhost:8787
```

Same engine, same web UI, same API — the container is the parity guarantee across
platforms. For a team deployment, set `CHAMP_STUDIO_ACCESS_TOKEN` in `.env` and share
`http://<host>:8787` + the token; teammates can also point the Mac app's engine URL at it.

## Distribute to your team

| Audience | Package | How |
|---|---|---|
| Mac designers | **`dist/ChampStudio-1.0.0.dmg`** — engine embedded, zero setup | Build: `./macapp/scripts/make_dmg.sh`. They drag to Applications, right-click → Open the first time (ad-hoc signed), add a key. Engine auto-starts (~30 s first launch). |
| Server / everyone via browser | **Multi-arch Docker image** (amd64+arm64) | See [docs/DEPLOY-SERVER.md](docs/DEPLOY-SERVER.md) — set `CHAMP_STUDIO_ACCESS_TOKEN`, share `http://server:8787`. |
| Windows devices | **`dist/ChampStudio-Windows-1.0.0.zip`** — desktop app; sets up Docker itself | Build: `desktop\windows\make-release.ps1`. They unzip and double-click `Install Champ Studio.cmd`; see [desktop/windows/README.md](desktop/windows/README.md). Without Docker: [windows/README-WINDOWS.md](windows/README-WINDOWS.md). |

## What's inside

| Path | What |
|---|---|
| `engine/` | FastAPI engine — providers, transforms, asset library (pytest suite) |
| `web/` | Buildless web UI (Champ Suite design language), served by the engine |
| `macapp/` | Native SwiftUI macOS app (SPM, no Xcode needed) |
| `docker/` + `docker-compose.yml` | Container packaging |
| `desktop/windows/` | Windows desktop app: installer, launcher with setup window, tray icon |
| `docs/superpowers/specs/` | Design spec |

## API in 20 seconds

`GET /api/health` · `GET/PUT /api/settings` · `GET /api/models/{images,videos,speech}` ·
`POST /api/images` (sync) · `POST /api/videos` → job → `GET /api/videos/jobs/{id}` ·
`POST /api/audio` (`{text, model?, voice?, speed?}` → audio asset) ·
`POST /api/lipsync` (`{image_asset_id, audio_asset_id, aspect_ratio?, resolution?}` → video job) ·
`GET /api/transform` + `POST /api/transform/{tool}` · `GET /api/assets` (+upload/file/delete,
lineage). Everything the two UIs do goes through this one API — build your own client if
you want.

## Derivative Lab tools

`vectorize` (color/binary SVG) · `skeleton` (1px structural spine, PNG+SVG) · `lineart` ·
`remove-bg` (local U²-Net) · `palette` (swatch PNG + CSS vars + JSON) · `halftone` ·
`posterize` · `upscale` (2×/4×). Outputs are assets too — chain them.

## Development

Engine tests run in the dev image (Python 3.12 + ffmpeg), no keys or network needed — OpenRouter is
faked with real media bytes:

```bash
./scripts/test-docker.sh                     # everything
./scripts/test-docker.sh tests/test_director.py -x
cd web/editor-src && npm install && npm test && npm run build   # editor (Vite + TypeScript)
```

UI audit (needs Node 22+ and Chrome, app running on :8787): crawls every view at desktop and phone
sizes, the editor at four window widths with its panels and dialogs, and the review page; writes
screenshots + `report.json` to `ui-audit-out/` and exits non-zero on console errors, failed
requests, invisible text, clipped labels, overflow or broken images.

```bash
node scripts/ui-audit/run.mjs [--project <editor sequence id>]
```


```bash
cd engine && uv venv --python-preference only-managed -p 3.12 .venv
uv pip install -p .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest -m "not slow"     # 19 tests
cd ../macapp && swift build                   # Mac app
```
