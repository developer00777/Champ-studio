# Champ Studio — Design Document

**Date:** 2026-07-18 · **Author:** Claude (autonomous session for Deep) · **Status:** Built from this spec

## What it is

Champ Studio is the evolution of Champ Video: a self-hosted AI creative studio where you
**create images, animate them into videos, and derive designer assets** (SVG vectors, line
art, skeletons, transparent cutouts, palettes, halftones) from any generation — all with
your own API keys. It ships as:

1. **Docker container** — identical behavior on Mac, Windows, Linux, or a cloud host.
2. **Native macOS SwiftUI app** — for Deep and the team, talking to the same engine.

## Research grounding (verified 2026-07-18)

- **OpenRouter image generation is real**: unified `POST /api/v1/images` — model slug,
  prompt, `n`, `resolution` (512/1K/2K/4K), `aspect_ratio`, `output_format`
  (png/jpeg/webp/**svg**), `background: transparent`, `input_references` for image-to-image.
  Returns base64 `b64_json` + `media_type`. Model discovery: `GET /api/v1/images/models`.
- **OpenRouter video generation is real** (launched April 2026): async
  `POST /api/v1/videos` → job id → poll `GET /api/v1/videos/{id}` → download
  `unsigned_urls`. Supports Sora 2, Veo 3.1, Seedance, Wan, Kling; `frame_images`
  (first/last frame) for image-to-video, `input_references`, `generate_audio`, `duration`,
  `resolution`, `aspect_ratio`. Model discovery: `GET /api/v1/videos/models`.
- **MuAPI** ("the map API") is the legacy/secondary BYOK provider from ChampVideo:
  `x-api-key` header, `POST /api/v1/{model}` → `request_id` → poll
  `/api/v1/predictions/{id}/result`.
- **Celsus vault decisions inherited**: OpenRouter is the group billing spine (drop-in
  replacement over MuAPI already happened in champ-video); backlog wanted video wiring,
  and accounts; SVG/vector tools are greenfield; use **Champ Suite design tokens**
  (`--champ-ink #0B0D12`, ember `#FF6B35`, Space Grotesk/Inter) rather than the inherited
  neon-green theme.
- **ChampOracle pattern reused**: env-driven config, retry-with-backoff, role/model registry.

## Architecture

One **engine**, three faces.

```
┌────────────────────────────────────────────────────────────┐
│  Champ Studio Engine (FastAPI, port 8787)                  │
│  ├── providers/ openrouter.py (primary) · muapi.py (alt)   │
│  ├── transforms/ vectorize · lineart · skeleton · rembg    │
│  │              palette · halftone · posterize · upscale   │
│  ├── library.py  (SQLite + assets on disk)                 │
│  └── serves web UI at /                                    │
└────────────────────────────────────────────────────────────┘
     ▲                  ▲                     ▲
  Web browser      Docker (Win/cloud)    macOS SwiftUI app
```

- **Engine** (Python 3.12, FastAPI + httpx + Pillow/OpenCV/scikit-image/vtracer/rembg):
  all provider calls, all processing, all storage. Data dir: `~/.champstudio` locally,
  `/data` in Docker (volume).
- **Web UI**: buildless single-page app served by the engine — what Docker users see.
- **Mac app**: native SwiftUI client for the same REST API. Stores keys in **Keychain**,
  auto-detects a local engine (and can launch it), or points at a teammate/cloud engine URL.
  Same API ⇒ identical behavior across faces.

## API contract (frozen for all clients)

- `GET /api/health` → `{status, version, keys: {openrouter: bool, muapi: bool}}`
- `GET/PUT /api/settings` → keys + defaults (stored `0600` in data dir; env wins)
- `GET /api/models/images`, `GET /api/models/videos` → merged provider model lists
- `POST /api/images` `{prompt, model, n, aspect_ratio, resolution, output_format,
  background, input_asset_ids?, provider?}` → sync; saves to library; returns assets
- `POST /api/videos` → `{job_id}`; `GET /api/videos/jobs/{id}` → status/asset (engine
  polls OpenRouter/MuAPI in background task)
- `POST /api/transform/{tool}` `{asset_id | upload, params}` → derived asset.
  Tools v1: `vectorize` (vtracer→SVG, color/binary modes), `lineart` (edges),
  `skeleton` (morphological thinning → strokes SVG+PNG), `remove-bg` (rembg),
  `palette` (k-means → JSON+CSS+PNG swatch), `halftone`, `posterize`, `upscale` (2×/4×).
- `GET /api/assets` (+`/api/assets/{id}`, `/file`, `DELETE`) — library with lineage
  (`parent_id` chains: image → video → vector … fully traceable).
- `POST /api/assets/upload` — bring your own image.

## Key decisions

- **Fresh repo, not a ChampVideo refactor** — user asked for a "completely fresh system";
  ChampVideo's Electron+Next+submodule stack stays untouched as reference.
- **Buildless web UI** — no node build in Docker; single HTML+JS+CSS with Champ tokens.
- **Engine-side polling** for video jobs so clients stay dumb and identical.
- **Keys**: `OPENROUTER_API_KEY` / `MUAPI_API_KEY` env, or runtime via settings UI —
  never logged (a past champ-video security fix), never leave the machine except to the
  provider.
- **No auth in v1** (single-user/team-LAN like ChampVideo today); optional
  `CHAMP_STUDIO_ACCESS_TOKEN` env gate for shared deployments; Clerk noted as v2 per
  project tracker.
- **Mac packaging without Xcode**: SwiftPM executable + `make_app.sh` bundling script
  (Info.plist + codesign ad-hoc), since only CLT is installed. Engine installed via `uv`
  (managed CPython 3.12 — local pyenv lacks lzma; verified working).

## Testing

- Unit tests for every transform on a synthetic test image (pytest).
- Live-server smoke: health, settings, transforms, library CRUD via curl.
- Provider clients tested against recorded schemas (no key burn); real-key path testable
  from the UI.
- `docker build` + container smoke test (daemon available in this session).
- `swift build` compile check + app bundle launch.

## Out of scope (v1)

Audio/TTS studio, lip sync, avatar pipelines (remain in ChampVideo), in/out-painting
canvas, Clerk accounts, auto-update. All fit the same engine contract later.
