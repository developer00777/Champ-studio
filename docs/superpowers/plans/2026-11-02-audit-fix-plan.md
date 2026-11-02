# Champ Studio — end-to-end audit and fix plan (2026-11-02)

## How the audit was done

- **Static wiring**: every DOM id referenced by `web/app.js` and `web/studio.js` checked against
  `index.html`; every `/api/...` call in the web app, review page and editor checked against the
  engine's 92 served routes (method included).
- **Live crawl** (headless Chrome over CDP, against the running Docker container): all 9 app views
  plus sub-tabs at 1440×900 and 390×844; editor at 1680×1000, 1280×720 and 1024×640 with every
  panel, dialog and the AI drawer; the review page; the main modals. Each state recorded console
  errors, failed requests, and in-page checks for low-contrast text, clipped labels, horizontal
  overflow, unlabelled buttons and broken images, plus a screenshot that was reviewed by eye.
- **Workflows**: Essential Graphics browse → insert → select in monitor → edit layer; server export
  of a motion-graphics sequence (frames checked); template thumbnails under real scrolling.
- **Tests**: engine 310 passed, editor 43 passed, editor typecheck and production build clean.

**Not covered live**: paid AI generation (Director produce, Animate, Lip Sync, Voice, Fill with AI)
— skipped to avoid spend; these paths run in the test suite against a fake provider. SSO/roles,
multi-user edit locks and the Mac app were not exercised.

## What is healthy

- 116/116 DOM references resolve; every UI API call has a matching route.
- 0 console errors and 0 failed requests across 21 app states and 13 editor states.
- 0 low-contrast text, 0 clipped labels, 0 broken images detected.
- All 84 template thumbnails load in about 9 s; the sidebar stays fixed while scrolling.
- Motion graphics work end to end, including the server export.

## Findings

Severity: **P1** = wrong behaviour users will hit; **P2** = visual / UX; **P3** = housekeeping.

| # | Sev | Area | What happens | Root cause | Fix |
|---|-----|------|--------------|------------|-----|
| 1 | P1 | Animate | Jobs list shows editor proxy, Director and render jobs as cards stuck on “rendering…” (42 of the 50 rows); real Animate videos get pushed out of the list. Lip Sync reads the same list. | `GET /api/videos/jobs` returns every job kind since the jobs table became shared (`engine/champstudio/main.py:326`). | Filter to `video` and `lipsync` kinds server-side; keep the client filter. |
| 2 | P1 | Animate / Lip Sync | Job dates read “Jan 21, 11:43 PM” (January 1970). | `fmtDate` (`web/app.js:64`) is given epoch seconds and treats them as milliseconds. | Accept numbers as seconds. |
| 3 | P1 | Editor | “Building proxies (2)…” never clears; every editor load re-queues ingest (33 duplicate ingest jobs so far) and polls for ~30 min. | Silent videos (most AI clips) never get `peaks.json`, but readiness requires peaks for every video (`engine/champstudio/ingest.py:45`). | Require peaks only when the probe says the file has audio; add a regression test. |
| 4 | P1 | Templates | Colour fields always submit `#F26722`, overriding the chosen brand kit (a Neutral-kit lower third comes out orange). | Colour input defaults to a hard-coded value and is always sent (`web/studio.js:397`). | Default to the selected kit's primary colour; send only when changed. |
| 5 | P1 | Review page | On a bad or missing link the header still says “Loading…” and Approve / Request changes stay clickable; with no token it requests `/api/review/null`. | `web/review.html:50,73,111` only swaps the main area on error. | Show the error state everywhere, hide the decision buttons, skip the request without a token, add a favicon. |
| 6 | P2 | Phone layout | The sidebar becomes a cramped horizontal strip with section labels inline (“MAKE”) and items cut off at the edge, on every view. | No small-screen navigation pattern. | Menu button + slide-in drawer (or bottom tab bar) under 640 px. |
| 7 | P2 | Editor | At ≤1100 px wide, Effect Controls and the top-bar buttons (Export, role badge) run off-screen and the page scrolls sideways. | Fixed four-column workspace. | Collapse the right panel into a tab/drawer under ~1200 px; show a “use a wider window” notice under ~900 px. |
| 8 | P2 | Editor | Graphic clips are plain grey on the timeline (their type badge is pink); there is no “New graphic” button in the timeline toolbar. | `CLIP_COLORS` has no `graphic` entry (`web/editor-src/src/timeline-view.ts:25`). | Add the colour; add a toolbar button next to Title. |
| 9 | P2 | Templates | Motion-graphics templates only offer “Create video” (renders the overlay on a flat background). | Modal is built for full-video templates. | For graphic templates, add “Add to sequence…” (pick or create an editor sequence and drop the graphic at the playhead). |
| 10 | P2 | Editor | Project-panel thumbnails of vertical clips are blurry. | Thumbnail strip tiles are 50×90 px, upscaled into ~110 px cards. | Generate taller tiles (≈160 px); re-ingest thumbnails lazily. |
| 11 | P2 | Create / Animate | “⌘ ↵ to run” hint shown on Windows (Ctrl+Enter is what works). | Static text in `web/index.html:146,250`. | Show Ctrl on non-Mac platforms. |
| 12 | P2 | Settings | Money shown with mixed precision ($0.000 vs $4.10); the 8th usage card wraps onto its own row. | Formatting / grid columns. | Two decimals (more only under $0.01); grid that fits 8 cards. |
| 13 | P2 | Director | Project cards titled by cutting the prompt at 60 characters mid-word (two cards read “…emails, an”); the plan's title never replaces it. | `engine/champstudio/routes/director.py:60`. | Trim on a word boundary with an ellipsis; rename to the plan title after planning. |
| 14 | P2 | Accessibility | Toggle switches (Autopilot, Transparent BG, Audio, template Voiceover / Music / AI video) have no accessible name. | `button.switch` sits next to a `<label>` that isn't linked to it. | Add `aria-labelledby` / `aria-label`. |
| 15 | P3 | Data | Jobs table grows forever (duplicate ingest jobs, every render). | No retention. | Prune completed ingest jobs after 7 days and other jobs after 90 days, at startup. |
| 16 | P3 | Code | Unused DOM ids `create-controls`, `lab-source`. | Leftover hooks. | Remove or wire up. |

## Plan

### Phase 1 — functional fixes (≈ half a day)

1. Jobs endpoint filter + date fix (#1, #2), with an API test that the Animate list contains only
   video / lip sync jobs.
2. Ingest readiness for silent video (#3), with a test using a silent clip; one-off cleanup of the
   duplicate ingest jobs.
3. Brand-aware colour defaults in the template form (#4).
4. Review page error state (#5).
5. Rebuild the container, re-run both test suites and the crawl below; confirm the proxies badge
   clears and Animate shows only real jobs.

### Phase 2 — visual and UX polish (≈ 1–1.5 days)

1. Phone navigation drawer (#6) and editor narrow-window layout (#7) — the two largest items.
2. Editor graphics touches (#8), “Add to sequence” for graphic templates (#9), sharper thumbnails
   (#10).
3. Small fixes: platform-aware shortcut hint (#11), Settings money formatting (#12), Director names
   (#13), switch labels (#14).
4. Re-crawl at all sizes and compare screenshots before/after.

### Phase 3 — guardrails (≈ half a day)

1. Keep the crawler in the repo (`scripts/ui-audit/`) with a one-line command that writes
   screenshots and a JSON report, so this audit can be repeated before each release.
2. Job retention (#15) and dead-id cleanup (#16).
3. Optional, needs approval: a live smoke run of the paid AI flows with a hard budget cap
   (~$1–2) to confirm the real providers still answer.

## Decisions needed

1. **Phone support** — full responsive navigation for the main app (recommended), and for the
   editor a “best on a desktop browser” notice rather than a phone layout?
2. **Paid smoke test** — OK to spend about $1–2 on one live run of each AI flow?
3. **Publishing** — the push to `developer00777/Champ-studio` (`main`) is still waiting on you
   running it or allowing it.
