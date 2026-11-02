# Champ Studio roadmap: AI video, template library and video editor

Date: 2026-10-30. Status: proposed, waiting for approval and an OpenRouter key.

## Goal

Grow Champ Studio from a one-clip-at-a-time generator into the team's video workspace:

1. **Brief to finished video** (InVideo AI equivalent). A requirement becomes a script, scenes, media, voiceover, captions and music, rendered as one video, then edited by chat command.
2. **Template library** (InVideo Studio equivalent). A library of B2B templates that an LLM can fill from a brief, with brand kits and every aspect ratio.
3. **Video editor** (Premiere Pro style, for the video team). A multi-track timeline editor with AI editing, interchange with Premiere, and team review.

All AI goes through one OpenRouter key, using the cheapest models that do the job well.

## Where the app is today

What works on `main`:

- One text-to-video or image-to-video clip per request, through OpenRouter's video models (`POST /api/videos`, polled in the background).
- Image generation, the Derivative Lab (vectorize, remove background, palette and others), and an asset library with lineage.

What's missing for "make me a video from this requirement":

- No LLM (chat) call, so nothing writes a script or plans scenes.
- No multi-scene assembly, voiceover, captions or music.
- No ffmpeg in the Docker image, so nothing can be composed or rendered.
- Jobs live in memory and disappear on restart.

Two things to fix before building on it:

- **Unmerged work.** The branch `feat/phase-1-audio-voice` holds 11 commits that are not on `main`: TTS (`POST /api/audio`), lip sync (`heygen/avatar-iv`), an audio asset kind, job kinds, redacting data URLs from stored params, and the corrected `frame_images` / `input_references` payload shapes. On `main`, image-to-video sends the wrong shape (`main.py:238`). This plan starts from that branch.
- **Expensive defaults.** The default video model is `google/veo-3.1`, which costs $0.40/s with audio, so a 30 s video is about $12. `google/veo-3.1-lite` costs $0.03–0.05/s.

## Model choices (OpenRouter catalog, pulled 2026-10-30)

Every model is a configurable role, so it can be swapped without code changes. Prices are per million input/output tokens or per output second.

| Role | Default | Price | Fallback | Why |
|---|---|---|---|---|
| Script, scene plan, template fill, chat edits | `anthropic/claude-haiku-5.5` | $0.10 / $0.50 | `openai/gpt-6-luna` ($0.10 / $0.50) | Strict structured output, tool use, 1M context, reliable JSON |
| Bulk and cheap text (tagging, captions cleanup, filler classification) | `deepseek/deepseek-v4.1-flash` | $0.045 / $0.50 | `inception/mercury-2.5` ($0.04 / $0.15) | Lowest cost with structured output |
| Watching footage (scene and shot understanding, B-roll matching) | `xiaomi/mimo-v2.6-flash` | $0.14 / $0.28 | `qwen/qwen3.8-omni-flash` ($0.15 / $0.47) | Takes video and audio input at flash prices |
| Draft video clips | `google/veo-3.1-lite` at 720p, no audio | $0.03/s | `x-ai/grok-imagine-video-1.5-lite` at 480p ($0.02/s) | Cheap previews before paying for finals |
| Final video clips | `google/veo-3.1-fast` at 1080p | $0.10–0.12/s | `kwaivgi/kling-v3.0-std` ($0.084/s) | About a quarter of the current default's cost |
| Long clips (up to 30 s) | `alibaba/wan-3.0` | $0.05 / $0.10 / $0.20 per s (480p / 720p / 1080p) | `bytedance/seedance-2.5` | Only cheap model that goes to 30 s |
| Talking head / presenter | `heygen/avatar-iv` | $0.05/s | — | Already wired on the phase-1 branch |
| Images (scene stills, first frames) | `google/gemini-3.1-flash-lite-image` | price to confirm with a key | `bytedance-seed/seedream-5-0-flash`, `black-forest-labs/flux-3-image-klein` | Image prices aren't in the public listing |
| Voiceover (TTS) | `google/gemini-3.8-flash-tts`, voice "Kore" | to confirm | other models from `/audio/speech` | Choice from the phase-1 plan |
| Transcription (word timestamps) | OpenRouter `/audio/transcriptions` | to confirm | — | 60 s upstream limit, so send audio in segments |
| Music beds | `google/lyria-3-clip-preview` | to confirm | owned music library | Commercial-use terms must be checked before ads use it |

**Rough cost of a 30 s, 6-scene video.**

| Mode | Cost |
|---|---|
| Draft (veo-3.1-lite, no audio) | about $0.90 for clips, plus a few cents for LLM, TTS and images; about $1 total |
| Final at 1080p (veo-3.1-fast) | about $3–3.60 total |
| Same video today (veo-3.1 with audio) | about $12 |
| Template videos built from stills, Ken Burns motion, text and voiceover | under $0.20 |

**Cost guardrails, built in Phase 0:**

- Show an estimate before every paid call.
- Keep a cost ledger per asset, project and user.
- Set budget caps per project and per month.
- Let a storyboard approval gate decide which scenes get paid video.

## Architecture

- **Engine.** Stays Python and FastAPI. New modules:
  - `llm.py`: OpenRouter chat with structured output, a model-role registry and a cost ledger.
  - `render/`: compiles a timeline into an ffmpeg command, plus a Pillow text rasterizer.
  - `director/`: brief to video.
  - `templates/`: template schema, brand kits, gallery.
  - `editor/`: project model, ingest, proxies, render queue.
  - `db.py`: persistent jobs, projects, comments, audit. SQLite now, written portably so Postgres is a configuration change.
- **One timeline model for everything.** Templates, Director output and editor projects all compile to a single internal timeline: tracks, then clips, using integer frames and a frame rate. OpenTimelineIO is the import and export format (Premiere XML, FCPXML, EDL through the OTIO plugins). The cp312 manylinux wheels install on `python:3.12-slim`. Template and effect details travel in `metadata["champ"]`.
- **Rendering on the server.** ffmpeg, with these filters: `filter_complex`, `trim`/`setpts`, `overlay`, `xfade`, `zoompan`, `sidechaincompress`, `loudnorm`. Text and captions are drawn by Pillow and composited as images, because ffmpeg builds without `drawtext` exist (found in Phase 0). Bundled OFL fonts keep output identical on Mac, Docker and Windows. MLT (`melt`) is the fallback if ffmpeg filter graphs become unmanageable.
- **Web UI.** The existing screens stay buildless. Only the editor uses Vite and TypeScript, because an NLE timeline is 15–30k lines of frame-accurate state where types prevent off-by-one-frame bugs. A Docker multi-stage build runs `vite build` in a `node:22-slim` stage. The runtime image and laptops never need Node.
- **Editor preview.** Plays 540p H.264 proxies through `<video>` elements composited on a canvas. Frame-accurate scrubbing and trimming use WebCodecs via Mediabunny (MPL-2.0). Waveforms use wavesurfer.js. Final export always renders on the server. Targets Chrome and Edge.
- **Build, don't adopt.** No existing editor fits a permissive license, a Python server and self-hosting without per-seat fees:
  - OpenCut is being rewritten.
  - designcombo and Remotion charge companies over 3 people, and Remotion needs a Node render stack.
  - Twick's server renderer doesn't run on Windows.
  - IMG.LY CE.SDK is the only realistic buy option (quote-only pricing). It is noted as a fallback.

## Template library design ("CST v1")

**What a template contains:**

- `scenes[]`: each scene has layers (media, text, shape, logo, captions) and a duration that is either fixed or fits the voiceover.
- `slots`: a JSON Schema of the editable fields (text, media, color, logo, audio, voiceover script). The same schema is the LLM's strict `response_format`, the UI form and the validator.
- Layers point at `{"$slot": …}` and `{"$brand": …}` and never hold raw values.
- Layer boxes are given per aspect ratio (16:9, 9:16, 1:1, 4:5), in normalized 0–1 coordinates.

**Brand kits** are separate objects: colors, fonts, light and dark logos, caption and lower-third styles, intro and outro, and voice. Rendering takes a template, a brand kit and an aspect ratio.

**How AI fills a template:**

1. Send the brief with the slot schema.
2. Validate the result.
3. Resolve media (upload, library, generate, or stock later).
4. Generate the voiceover with TTS.
5. Transcribe it for word timings.
6. Lay out and render.

Chat edits ("make scene 2 red", "replace scene 3") come back as a JSON Patch checked against the same schema.

**Template to editor.** A template compiles into an editor project. Each clip keeps a back-reference to its slot and scene, so re-filling slots works even after manual edits.

**Starter library: 52 templates in 10 groups,** each in four aspect ratios:

| Group | Count |
|---|---|
| Social ads | 7 |
| Product and SaaS promo | 6 |
| Explainers | 5 |
| Data and thought leadership | 7 |
| Social proof | 4 |
| Events and webinars | 6 |
| Recruitment | 6 |
| Lead gen and sales enablement | 5 |
| Brand utilities (intro, outro, lower third, caption styles, stingers) | 5 |
| Seasonal and company news | 1 |

The full list is in the research notes.

**Licensing:**

- Nothing is copied from InVideo, Canva, CapCut or Envato. Every template is authored in-house.
- Fonts are Google Fonts (OFL), bundled.
- Stock (Pexels or Pixabay, free API keys) is an optional later add-on with a provenance log, because the plan is "OpenRouter key only".

## Phases

Estimates assume one developer plus AI agents. They are judgement, not measurements. Every phase ends with tests passing, a Docker build, and a live smoke test under a spending cap.

### Phase 0: Foundation (about 1 week)

- [ ] Branch `feat/studio-roadmap` from `feat/phase-1-audio-voice`. Merge it to `main` once it's green.
- [ ] Add ffmpeg and bundled fonts (Inter, plus 5–6 OFL families for templates) to the Docker image and the Windows scripts.
- [ ] Persistent `db.py`: jobs (they survive restarts), projects, a cost ledger, and settings for model roles.
- [ ] `llm.py`: OpenRouter `/chat/completions` with strict JSON-schema output, retry with backoff, and fallback to the role's second model.
- [ ] Model-role registry with the defaults above. Change the default video model to `veo-3.1-lite`.
- [ ] Cost estimator endpoint and budget caps.
- [ ] Render worker v0: timeline JSON → ffmpeg (cuts, image and video layers, Pillow text, crossfades, audio mix and ducking, loudnorm). Progress comes from `-progress`.
- [ ] Tests: provider tests through `httpx.MockTransport` (no paid calls), golden-frame render tests on synthetic media.

### Phase 1: Brief to video, "Director" (2–3 weeks)

- [ ] Brief form (goal, audience, platform, length, tone, aspect ratio, brand kit) → LLM script and scene plan (JSON schema).
- [ ] Storyboard approval gate. Each scene's still comes from the image model and is approved or regenerated before any paid video is made.
- [ ] Per-scene media: still with Ken Burns motion (cheap), image-to-video draft, then final. Optional avatar-presenter scenes.
- [ ] Voiceover per scene (TTS), then transcription for word timings. Scene lengths fit the voiceover.
- [ ] Captions: word-level, caption styles from the brand kit, burned in, plus SRT and VTT export.
- [ ] Music bed (Lyria once its terms are confirmed, or the owned library), ducked under the voiceover.
- [ ] Render in 16:9, 9:16, 1:1 and 4:5 from one plan.
- [ ] Chat-command editing: LLM → JSON Patch on the plan → re-render only the changed scenes.
- [ ] "Open in editor" hands off to Phase 3.

### Phase 2: Template library (3–4 weeks)

- [ ] CST v1 schema and validator. Template renderer on the Phase 0 render worker.
- [ ] Brand kit manager: colors, fonts, logos, styles, voice. Start with a kit per Champions Group brand (LakeB2B, Ampliz, SPAN, RecruitChamp, and others).
- [ ] Gallery UI: category and aspect filters, animated previews, search.
- [ ] Form auto-generated from each template's slot schema, with a live preview.
- [ ] "Fill with AI" from a brief. Batch fill from a CSV for personalized prospect videos with merge fields.
- [ ] Author the 52 starter templates. The first 15 (social ads, data reels, hiring, webinar, testimonial) ship before the rest.
- [ ] Save any Director video or editor project as a new template (the team grows the library).

### Phase 3: Editor MVP (6–8 weeks)

- [ ] Vite and TypeScript editor module. Internal timeline model with OTIO import and export.
- [ ] Ingest: ffprobe, conversion of variable-frame-rate phone footage, 540p proxies, thumbnail strips, waveform peaks. These are stored as derived assets in the lineage graph.
- [ ] Project panel with bins, source monitor (In/Out, insert, overwrite), program monitor.
- [ ] Canvas timeline with any number of video and audio tracks, mute, solo and lock.
- [ ] Tools: select, razor, ripple and roll trim, ripple delete, J and L cuts, snapping, markers.
- [ ] Shortcuts: JKL, I and O, Q and W, Ctrl+K, comma and period.
- [ ] Effect controls (position, scale, rotation, opacity, anchor) with linear and ease keyframes.
- [ ] Transitions: dissolve, dip, wipe, audio crossfade. Titles and lower thirds from the brand kit.
- [ ] Audio: clip and track volume, fades, meters. Constant speed change.
- [ ] Undo and redo through the command pattern. Autosave every 2 s. Numbered project versions.
- [ ] Export presets: H.264 in 16:9, 9:16 and 1:1; SRT and VTT. Server render queue with progress.

### Phase 4: AI editing (4–6 weeks)

- [ ] Text-based editing: delete words in the transcript to cut the timeline. Every later step reads timeline time through an explicit mapping from source time to timeline time; the old project had a bug from reusing raw timestamps.
- [ ] Filler-word and silence removal (`silencedetect` plus LLM classification), with padding and fades at each cut.
- [ ] Auto captions with karaoke-style word highlights.
- [ ] LLM rough cut from a brief.
- [ ] B-roll: suggestions and placement from the library or generated clips.
- [ ] Auto-reframe from 16:9 to 9:16, done locally with OpenCV face and saliency detection and a smoothed crop.
- [ ] Smart ducking written as volume keyframes, so it stays editable.
- [ ] Loudness to -14 LUFS for social.
- [ ] Scene-edit detection. Speech enhancement (`arnndn`).

### Phase 5: Pro editing (6–8 weeks)

- [ ] Slip and slide tools. Nested sequences. Adjustment layers.
- [ ] Bezier keyframes and a graph editor.
- [ ] Motion graphics templates (MOGRT-like) from CST.
- [ ] Basic color (exposure, contrast, white balance, saturation), LUTs and scopes.
- [ ] Variable speed, reverse and freeze frame. Multicam (basic).
- [ ] ProRes and DNxHR export.
- [ ] Premiere XML, FCPXML and EDL import and export through OTIO. Effects don't carry over; that is the industry norm.

### Phase 6: Enterprise (6–10 weeks)

- [ ] Deployment profile with Postgres and MinIO/S3. Local disk stays the laptop default.
- [ ] OIDC single sign-on (Entra ID, Google or Keycloak). Roles: viewer, reviewer, editor, admin.
- [ ] Audit log, built on the editor's command log.
- [ ] Per-sequence locking for team projects.
- [ ] Review and approval: share links, frame-accurate comments with on-frame drawing, version stacks, approval states. Comments show as editor markers.
- [ ] Render worker pool. Per-team budgets and usage reports.
- [ ] Content-credentials metadata on export.
- [ ] Stretch goal: real-time co-editing with Yjs.

**Total: about 6–8 months to an enterprise-ready editor; Director and templates are usable after about 6–8 weeks.** Matching Premiere Pro feature for feature is not the goal. The goal is a Premiere-style editor that covers what a marketing video team uses, plus AI features Premiere doesn't have.

## Testing

- **Unit tests:** pytest for every module, with providers mocked so tests cost nothing.
- **Render tests:** golden-frame tests for the timeline-to-ffmpeg compiler. Synthetic media, fixed frames compared within a tolerance.
- **LLM tests:** recorded responses. Plus a small live evaluation set (10 briefs, schema-valid rate, judged quality) run when model defaults change.
- **Editor tests:** Playwright end-to-end (import, cut, trim, undo, export).
- **Live smoke test per phase:** real key with a capped budget. Each Phase 1 and 2 live run is targeted under $2.
- **Docker:** build plus a container smoke test every phase.

## Risks

| Risk | Mitigation |
|---|---|
| A/V drift from variable-frame-rate phone footage | Convert to constant frame rate on ingest |
| Editor preview doesn't match the export | One timeline evaluator spec; golden-frame tests |
| Keyframes too complex for ffmpeg filter graphs | MLT fallback, or render overlays per frame |
| Model prices or availability change | Role registry plus live catalog check at startup |
| AI media licensing (music, likeness) | Check upstream commercial terms; consent for avatars; provenance log |
| Scope (Premiere parity) | Phases ship usable value independently; Director and templates first |

## Decisions needed

1. Approve the phase order: Director and templates first, then the editor. This is the recommended order.
2. Approve the Vite and TypeScript build for the editor module only. Recommended.
3. Music: Lyria through OpenRouter (if commercial terms allow), or a small owned music library.
4. Stock footage later: free Pexels or Pixabay keys, or AI-generated and uploaded media only.
