# Phase 0 findings: Champ Studio consolidation

Date: 2026-09-30. Scope: the Phase 0 spike from the consolidation plan. Read: the `video-automation` backend (`/Users/deep/Projects/video-automation`, 5,043 lines of Python), its render service (`frontend/render-service.ts`, `frontend/lib/composition.ts`), the ChampVideo studio package, the Champ Studio engine, and OpenRouter's current docs and public model catalog. Nothing was changed in any repo other than adding this note.

## The three questions

**1. Overlays: ffmpeg, no sidecar.** The old overlay layer is simpler than its stack suggests: 2-word uppercase captions toggled on and off, a lower third that fades and slides in, bullets, a watermark, a title card and an outro. A spike reproduced captions, animated lower third and watermark with Pillow-rendered PNGs and one ffmpeg `overlay` pass: 0.64 s to composite a 6 s 720p clip, frames checked by eye. The HyperFrames path it replaces averaged 52 to 71 s per chunk in the recorded job logs and failed 9 of 17 compose jobs.

Three constraints follow:

- Text must be rasterised by Pillow. This machine's Homebrew ffmpeg 8.0.1 has no `drawtext`, `subtitles` or `ass` filter, so any design that burns text inside ffmpeg fails here.
- The Docker image has no ffmpeg at all. It needs adding, as does a bundled font (Inter is OFL) so output matches across Mac, Docker and Windows.
- The self-contained Mac DMG and the Windows scripts need an ffmpeg binary too.

**2. `frame_images` plus `input_references`: not combinable on the video endpoint.** OpenRouter's docs state that when both are sent, `frame_images` takes precedence and the request is treated as image-to-video. Director's design survives, because character consistency belongs one stage earlier: the image API accepts up to 16 `input_references`, so character sheets shape each first-frame still, and the video call carries only the approved frame. The plan's mitigation "character sheets on every frame" is true for stills and not for clips.

Not run live: there is no OpenRouter key in `~/.champstudio` or the environment, and the key stored in ChampVideo returned 401 on its last use. One paid call (under $0.50) is still needed, because the payload shapes in Champ Studio do not match the current schema:

- `engine/champstudio/main.py:238` sends `{"url", "frame_type"}`; the schema is `{"type": "image_url", "image_url": {"url"}, "frame_type"}`.
- `engine/champstudio/main.py:169` sends reference images as plain strings; the schema is the same `image_url` object.

Useful for Director: `previous_job_id` continues a finished clip on models that support it, Seedance 2.0 Mini can return its last frame, and Kling v3 is 720p only, so 480p draft mode needs Wan 3.0 ($0.05/s), Seedance or Grok Imagine.

**3. Data worth migrating: almost none on this machine.** Two databases hold 7 projects (4 finished, 3 failed), 5 final videos of 9 to 35 s, 4 voiceover files and 0 generated clips, about 60 MB in total. Import the finished videos through the existing upload endpoint and skip the project records. ChampVideo is a local app, so teammates' machines need the same check before Phase 5 is sized.

## What the plan did not know

- **OpenRouter now covers most of Phase 1.** It has a dedicated `/audio/speech` endpoint (21 models), `/audio/transcriptions` with word timestamps, and `heygen/avatar-iv` as a video model (photo plus text or audio in, lip-synced talking head out, $0.05/s). ChampVideo's streaming-PCM text-to-speech workaround is obsolete. Audio, lip sync and Presenter may need no ElevenLabs, MuAPI or HeyGen key. A direct HeyGen key matters only if the team keeps using account avatars, which every existing project does (`avatar_id` plus `voice_id`).
- **Transcription has a 60 s upstream timeout.** Edit must extract audio and send it in segments.
- **Edit Studio has never finished a run here, and has a timing bug.** `orchestrator.py:432-439` transcribes the raw footage, removes silence, then cuts the cleaned video using the raw timestamps, so cuts and captions drift after the first removed pause. Captions also ignore word timestamps (cues are spread evenly) and the six templates are ignored by the renderer. Port the design and fix these, do not copy the code.
- **Generate Studio has never finished a run here either**, so the Phase 3 side-by-side test has no baseline yet.
- **Five months of backend work is uncommitted.** `video-automation` has 1,818 changed lines and 6 untracked modules, including all of Edit Studio, last commit 2026-04-30. Commit and push it before porting.
- **The changelog is not a reliable guide.** Its two-pass `blend=screen` render exists in neither the source nor the installed app.

## Champ Studio gaps to close before Phase 1

- The library has no audio kind and rejects audio uploads (`library.py:48`, `main.py:324`).
- Jobs live in memory (`jobs.py:23`) and vanish on restart. Director's approval gates need them persisted.
- `jobs.py:77` stores the full base64 first frame in each video's `params`, which bloats the database and gets worse with Director.
- The OpenRouter provider has no chat call, which the story bible and shot list need.

## Effect on the estimates

| Phase | Plan | After Phase 0 | Why |
|---|---|---|---|
| 1. Audio and voice | 4 to 5 days | about 3 days | Mostly new methods on the existing OpenRouter provider |
| 2. Edit and Presenter | 5 to 6 days | holds | About 900 lines port directly; the overlay renderer is a rewrite (about 250 lines) and the timing bug needs fixing |
| 3. Director | 8 to 10 days | holds | Design confirmed; add job persistence |
| 5. Migrate and retire | 3 days | about 1 day | Little data to move, pending teammates' machines |

## Decisions

- **2026-10-01: OpenRouter only for v1.** No direct HeyGen, ElevenLabs or MuAPI providers in Phases 1 and 2. Text-to-speech, transcription, lip sync and Presenter all go through the existing OpenRouter provider (`/audio/speech`, `/audio/transcriptions`, `heygen/avatar-iv`). MuAPI stays as it is today, the secondary image and video provider.

## Open items

1. Live test pending (2026-10-01): needs a working OpenRouter key; run the three curl calls in docs/superpowers/plans/2026-10-01-phase-1-audio-voice.md Task 9 Step 4 and record voice / image-to-video / lip sync results here.
2. The plan's three decisions: Mac app as a shell, Windows via Docker, retirement date.

Spike script: `overlay_spike/spike.py` in the session scratchpad. It is the seed of `edit/overlays.py`.
