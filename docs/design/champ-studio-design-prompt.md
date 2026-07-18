# Champ Studio — Product Design Prompt ("Rival Higgsfield")

> Hand this prompt to a product designer, a design agent (Figma Make, v0, Lovable,
> frontend-design skill), or a Claude session with the repo open. It is grounded in
> July-2026 research on Higgsfield AI and in Champ Studio's shipped v1.0
> (engine API is frozen — design against it, don't invent endpoints).

---

## THE PROMPT

You are designing **Champ Studio 2.0** — Champions Group's self-hosted AI creative
suite. v1.0 already ships a working engine (FastAPI :8787), a web UI, a native Mac
app, and a Docker image. Your job is to redesign the product experience so a
creator who lives in Higgsfield, Krea, or Freepik feels *at home in the first ten
seconds — then finds things none of those can do*.

### 1. Know the benchmark (Higgsfield, mid-2026)

Higgsfield's winning patterns you must match in spirit:

- **Preset-first creation.** Users rarely start from a blank prompt: they start from
  a wall of *named, thumbnailed presets* ("Viral Presets": Baseball Game, Drift
  Racing; 50+ Soul photo styles; 20+ VFX like Style Snap, Plushies, Recast). A card
  = a promise of an outcome. One click loads prompt + model + params, pre-filled and
  editable.
- **Guided modes over raw parameters.** The Create workspace is a row of modes —
  Image-to-Video, Draw-to-Video, Speak (lip-sync), Soul ID (character consistency),
  UGC Builder, Product Builder, Inpaint — each a wizard-thin flow, not a settings
  panel.
- **Director-level video.** Cinema Studio 3.5: an *AI Director* breaks a concept
  into individual shots; each shot gets real camera grammar (dolly, truck, pan,
  tilt, crash zoom, FPV, bullet time) as first-class controls, not prompt hacks.
- **One roof, many engines.** Sora 2, Veo 3.1, Kling 3.0, Seedance 2.0, Wan 2.6,
  Flux, GPT Image, Seedream 5.0 — wrapped in **unified controls** so switching
  models never changes the UI.
- **An apps library, not a feature list.** 80+ single-purpose apps (Face Swap,
  Headshot Generator, Skin Enhancer, Product Placement, Outfit Swap, Angles 2.0…)
  each with its own landing card. Small tools feel like products.
- **Community as fuel.** Explore feed of public generations with creator
  attribution; remix from any card; contests ($100K app contest) and Originals
  (streaming AI series) keep the surface alive.
- **Post-production in-suite.** Brush inpaint, video edit, Topaz-grade upscale,
  video background removal, color grading, image expansion — the asset never has to
  leave.

Their visual language: dark UI, **lime/neon accents**, huge media-first cards,
template-driven onboarding. Their weakness (from reviews): credit anxiety, opaque
pricing, and everything lives on *their* servers.

### 2. Champ Studio's unfair advantages (design around these)

1. **Self-hosted + BYOK.** No credits, no upload of client work to someone else's
   cloud. Design must *radiate* this: "Your keys. Your machine. Your assets."
   Show per-generation **real provider cost** (the engine returns `cost`) instead of
   credits — honesty as a feature.
2. **The Derivative Lab.** Local, free, instant transforms no competitor bundles:
   true SVG vectorization, structural skeletons, line art, local background
   removal, palettes, halftones. Designers get *production assets*, not just
   pictures.
3. **Lineage.** Every asset knows its ancestry (image → video → vector →…). Design
   the **lineage graph as a hero feature** — a visual family tree of every creation,
   clickable, remixable at any node. Nobody else shows this.
4. **Three faces, one engine.** Web (Docker, Windows-identical), native Mac app,
   and a clean REST API. The design system must serve all three.

### 3. Information architecture (design all of it)

- **Home / Explore** — local-first "community" feed: your team's shared engine
  becomes the community. Wall of recent generations from the library, preset wall,
  "start from this" remix on every card.
- **Create** (images) — floating prompt dock + **mode chips**: Text-to-Image,
  Reference (multi-image, engine supports `input_asset_ids`), Transparent/Sticker,
  SVG-native (OpenRouter `output_format: "svg"`), Poster (text-heavy models).
  Preset wall above the fold; model picker is secondary, params tertiary.
- **Animate** (video) — modes: Text-to-Video, Image-to-Video (first-frame from any
  library asset), and **Shot Builder**: a lightweight Cinema-Studio answer where a
  concept expands into N shot cards (prompt + camera-move chip per shot: dolly-in,
  orbit, crash zoom, FPV, static), each shot = one engine video job; storyboard rail
  shows job states. (Engine: async jobs, 3s poll — design the wait as a feature:
  shimmering storyboard frames, cost ticker, "notify when done".)
- **Derivative Lab** — the differentiator. Design it like a *chemistry bench*:
  source asset left, tool cards right, chained results as a horizontal reaction
  strip with lineage breadcrumbs. Every output: "Use as source" (chain), "Animate",
  "Download". Batch mode: run one tool across N assets.
- **Library** — filterable grid (Images / Videos / Vectors / Data) + **Lineage
  view** (the graph). Multi-select → batch transform / delete / export pack
  (zip of asset + its whole derivative family — "Brand Pack" export).
- **Apps** (v2 surface) — reframe each transform + each guided mode as a named app
  card with thumbnail, à la Higgsfield's 80+ wall. Same engine calls, marketed
  surface. Leave slots for: Face Swap, Character Lock (reference-consistency),
  Product Shots, Ad Maker (concept → image → video → pack).
- **Settings** — keys (OpenRouter, MuAPI) with per-key status + live model counts,
  engine URL (local vs team server), access token, data directory, cost history.

### 4. Visual language — Champ, not a Higgsfield clone

Use the **Champ Suite tokens** (in repo: `web/styles.css`): ink `#0B0D12`
background, carbon `#14171F` panels, slate `#1E222D` cards, steel `#2A2F3D`
borders, cloud `#D1D5DB` text, mist `#6B7280` secondary. Accent is **Champ Ember
`#FF6B35`** (glow `#FF8C5A`) — warm fire against cool ink, deliberately opposite to
Higgsfield's lime. Type: Space Grotesk (display), Inter (body), JetBrains Mono
(params/costs). Wordmark: "Champ" white + "Studio" ember.

Art direction: media-first (the generations are the UI), glassy depth, 16px radii,
shimmer loaders, ember focus rings, no gratuitous gradients. Empty states teach
("Nothing here yet — try a preset"). Motion: 150–250ms ease-out; storyboard/job
cards pulse subtly while pending. Density: Krea-like calm, not dashboard clutter.

### 5. Constraints (hard)

- Engine API is frozen (see `README.md` "API in 20 seconds" + `engine/champstudio/main.py`).
  New surfaces (presets, shot builder, apps) are **client-side compositions** of
  existing endpoints + a static JSON preset catalog — no backend redesign required.
- Web UI stays buildless (HTML/CSS/ES modules, no framework, no CDN dependencies
  beyond Google Fonts with fallbacks).
- Transforms accept raster images only; vectors are terminal outputs.
- Must degrade gracefully with zero keys (Derivative Lab + Library fully usable —
  make that the free-feeling on-ramp).
- Responsive to ~900px; the Mac app mirrors the same IA in SwiftUI.

### 6. Deliverables

1. Screen designs for all seven IA areas (desktop-web first, Mac-app variant notes).
2. A preset catalog spec: card anatomy (thumb, name, one-line promise, model+params
   payload), 24 launch presets across photo/cinema/sticker/vector/poster styles.
3. Shot Builder flow: concept → shot cards → storyboard rail → per-shot job states →
   assembled result view.
4. Lineage graph component: node = asset thumb, edge = tool/model, actions on node.
5. Component library on the Champ tokens (buttons, chips, prompt dock, cards, rails,
   toasts, modals) reusable across web + SwiftUI.

**North star:** *Higgsfield sells magic on their servers. Champ Studio hands you the
whole magic kit — on yours.* Every screen should make a designer feel they own the
factory, not just the outputs.

---

## Sources (research, July 2026)

- [Higgsfield homepage](https://higgsfield.ai/) · [Soul](https://higgsfield.ai/soul) ·
  [Marketing Studio](https://higgsfield.ai/marketing-studio-intro) ·
  [AI Design Generator](https://higgsfield.ai/ai-design-generator) ·
  [Product Photography](https://higgsfield.ai/ai-product-photography)
- [Higgsfield 2026 full feature guide](https://geo.higgsfield.ai/higgsfield-ai-features-full-guide-2026)
- Reviews: [Scribe 2026 review](https://scribehow.com/o/YH6J12I6QXuxrhOYUf8sUg/page/Higgsfield_AI_Review_2026_I_Tested_Every_Feature__Heres_My_Honest_Verdict__CxSf_04eSF6MvTYWFWdS7Q) ·
  [AppReviewLab](https://appreviewlab.com/higgsfield-ai-review-2026/) ·
  [JustPickAi](https://justpickai.com/blog/higgsfield-ai-review-2026) ·
  [AI Tool Curator](https://www.aitoolcurator.com/ai-tools/design-creativity/higgsfield/) ·
  [gaga.art](https://gaga.art/blog/higgsfield-ai/)
- [OpenAI on Higgsfield](https://openai.com/index/higgsfield/) ·
  [MindStudio: Claude Code + Higgsfield](https://www.mindstudio.ai/blog/build-brand-ad-creative-claude-code-higgsfield-5-minutes)
