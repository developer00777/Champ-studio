// AI drawer: text-based editing (transcript), silence/filler removal,
// captions, ducking, reframe, b-roll, scene detection, speech enhancement,
// rough cut and chat commands. Every returned timeline is applied as ONE
// undoable command.

import { ApiError, get, post, waitJob, type Asset, type Job, type Project, type Transcript, type TranscriptWord } from "../api";
import { autosave } from "../autosave";
import { media } from "../media";
import { ASPECTS, clipEnd, findClip, normalizeTimeline, type Clip, type Timeline } from "../model";
import * as ops from "../ops";
import { store } from "../store";
import { button, clear, h, icon, iconButton, progressBar, toast } from "../ui";
import { openProject } from "./dialogs";

type Tab = "transcript" | "tools" | "chat";

interface RunOpts {
  label: string;
  /** Apply `result.timeline` to the document (default true). */
  applyTimeline?: boolean;
}

export class AiDrawer {
  readonly el: HTMLElement;
  private tabBody: HTMLElement;
  private tab: Tab = "transcript";
  private status: HTMLElement;
  private prog = progressBar();
  private cancelBtn: HTMLButtonElement;
  private running: { jobId: string | null; abort: AbortController } | null = null;
  private transcriptFor = "";
  private transcript: Transcript | null = null;
  private wordSel: [number, number] | null = null;
  private wordEls: HTMLElement[] = [];
  private chatLog: Array<{ who: "you" | "ai"; text: string }> = [];
  getSelectedAssets: () => string[] = () => [];
  open = false;
  private wordDrag = false;

  constructor() {
    this.tabBody = h("div", { class: "drawer-body" });
    this.cancelBtn = h("button", { class: "btn btn-sm btn-danger", type: "button", onclick: () => void this.cancel() }, "Cancel");
    this.status = h("div", { class: "ai-status", style: "display:none" }, this.prog.el, this.cancelBtn);
    const tabs = h("div", { class: "seg", role: "tablist" });
    for (const [id, label] of [
      ["transcript", "Transcript"],
      ["tools", "Tools"],
      ["chat", "Chat"],
    ] as const) {
      tabs.append(
        h("button", {
          class: `seg-btn${id === this.tab ? " on" : ""}`,
          type: "button",
          role: "tab",
          "data-tab": id,
          onclick: () => {
            this.tab = id;
            tabs.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("on", (b as HTMLElement).dataset.tab === id));
            this.render();
          },
        }, label),
      );
    }
    this.el = h(
      "aside",
      { class: "drawer", "aria-label": "AI editing", "aria-hidden": "true" },
      h("div", { class: "drawer-head" }, icon("sparkle"), h("h2", null, "AI editing"), h("span", { class: "grow" }), iconButton("close", "Close AI panel", () => this.toggle(false), "Esc", "btn icon-btn btn-ghost")),
      tabs,
      this.status,
      this.tabBody,
    );
    store.on("selection", () => this.open && this.tab !== "chat" && this.render());
    window.addEventListener("pointerup", () => (this.wordDrag = false));
    let raf = 0;
    store.on("playhead", () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => this.highlightPlayheadWord());
    });
  }

  toggle(on = !this.open): void {
    this.open = on;
    this.el.classList.toggle("open", on);
    this.el.setAttribute("aria-hidden", String(!on));
    if (on) this.render();
  }

  private clip(): Clip | null {
    return store.primary()?.clip ?? null;
  }

  private render(): void {
    clear(this.tabBody);
    if (this.tab === "transcript") this.renderTranscript();
    else if (this.tab === "tools") this.renderTools();
    else this.renderChat();
  }

  // ── job runner ──

  private async run<R extends Record<string, unknown>>(path: string, body: Record<string, unknown>, o: RunOpts): Promise<R | null> {
    if (store.readOnly && o.applyTimeline !== false) {
      toast(store.readOnlyReason || "Read-only", "warn");
      return null;
    }
    if (this.running) {
      toast("Another AI job is running — wait for it or cancel it", "warn");
      return null;
    }
    const rev = store.revision;
    const abort = new AbortController();
    this.running = { jobId: null, abort };
    this.status.style.display = "";
    this.prog.set(0, `${o.label}: starting…`);
    try {
      const r = await post<{ job_id?: string } & R>(path, body, abort.signal);
      let result: R;
      if (r.job_id) {
        this.running.jobId = r.job_id;
        const job = await waitJob<R>(r.job_id, { signal: abort.signal, onProgress: (j: Job) => this.prog.set(j.progress ?? 0, `${o.label}: ${j.stage || j.status}`) });
        result = (job.result ?? {}) as R;
      } else result = r;
      const tl = (result as { timeline?: Timeline }).timeline;
      if (tl && o.applyTimeline !== false) {
        if (store.revision !== rev && !window.confirm("The sequence changed while the AI was working. Apply its result anyway? (Edits made in the meantime will be replaced; you can undo.)")) return null;
        store.apply(o.label, normalizeTimeline(tl));
      }
      const summary = (result as { summary?: string }).summary;
      toast(summary ? `${o.label}: ${summary}` : `${o.label}: done`, "success", 7000);
      return result;
    } catch (e) {
      if ((e as Error).name === "AbortError") return null;
      if (e instanceof ApiError && e.unavailable && !this.running?.jobId) toast(`${o.label} isn't available on this engine yet (${path} → ${e.status}).`, "warn", 7000);
      else if (e instanceof ApiError && e.status === 499) toast(`${o.label} canceled`, "info");
      else toast(`${o.label} failed: ${(e as Error).message}`, "error", 8000);
      return null;
    } finally {
      this.running = null;
      this.status.style.display = "none";
    }
  }

  private async cancel(): Promise<void> {
    const r = this.running;
    if (!r) return;
    if (r.jobId) await post(`/api/jobs/${r.jobId}/cancel`).catch(() => undefined);
    r.abort.abort();
  }

  // ── transcript ──

  private renderTranscript(): void {
    const c = this.clip();
    const body = this.tabBody;
    if (!c || !(c.type === "video" || c.type === "audio") || !c.asset_id) {
      body.append(h("div", { class: "empty" }, icon("text", 24), h("p", null, "Select a video or audio clip."), h("p", { class: "muted" }, "Its transcript appears here. Select words and press Delete to cut them from the clip (and its linked audio).")));
      return;
    }
    const assetId = c.asset_id;
    if (this.transcriptFor !== assetId) {
      this.transcriptFor = assetId;
      this.transcript = null;
      this.wordSel = null;
      body.append(h("p", { class: "muted" }, "Loading transcript…"));
      // the media status advertises a transcript before we request it (avoids a 404)
      media
        .ensure(assetId)
        .catch(() => null)
        .then((st) => (st?.transcript ? get<Transcript>(`/api/assets/${assetId}/transcript`) : Promise.reject(new ApiError(404, "no transcript yet"))))
        .then((t) => {
          if (this.transcriptFor !== assetId) return;
          this.transcript = t;
          this.open && this.tab === "transcript" && this.render();
        })
        .catch((e) => {
          if (this.transcriptFor !== assetId) return;
          this.transcript = { text: "", words: [], duration: e instanceof ApiError && e.status === 404 ? -1 : -2 };
          this.open && this.tab === "transcript" && this.render();
        });
      return;
    }
    const t = this.transcript;
    if (!t) {
      body.append(h("p", { class: "muted" }, "Loading transcript…"));
      return;
    }
    if (!t.words.length) {
      const lang = h("input", { class: "input", type: "text", placeholder: "auto", "aria-label": "Language code (optional)", style: "max-width:90px" });
      body.append(
        h(
          "div",
          { class: "empty" },
          h("p", null, t.duration === -2 ? "Couldn't load the transcript." : "This clip hasn't been transcribed yet."),
          h(
            "div",
            { class: "row gap" },
            lang,
            button("Transcribe", async () => {
              const r = await this.run<Record<string, unknown>>(`/api/assets/${assetId}/transcribe`, lang.value ? { language: lang.value } : {}, { label: "Transcribe", applyTimeline: false });
              if (r) {
                await media.refresh(assetId);
                this.transcriptFor = "";
                this.render();
              }
            }, "btn btn-primary", "sparkle"),
          ),
        ),
      );
      return;
    }
    const fps = store.fps;
    const srcIn = c.in / fps;
    const srcOut = (c.in + c.duration * c.speed) / fps;
    const box = h("div", { class: "transcript", tabindex: 0, role: "textbox", "aria-readonly": "true", "aria-label": "Transcript — select words, then press Delete to remove them" });
    this.wordEls = [];
    let anchor = -1;
    t.words.forEach((w, i) => {
      const inside = w.end > srcIn && w.start < srcOut;
      const el = h("span", { class: `word${inside ? "" : " outside"}`, "data-i": String(i), title: `${w.start.toFixed(2)}s` }, w.word);
      el.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        box.focus();
        if (e.shiftKey && this.wordSel) this.setSel(Math.min(this.wordSel[0], i), Math.max(this.wordSel[1], i));
        else this.setSel(i, i);
        anchor = e.shiftKey ? anchor : i;
        this.wordDrag = true;
      });
      el.addEventListener("pointerenter", () => {
        if (this.wordDrag && anchor >= 0) this.setSel(Math.min(anchor, i), Math.max(anchor, i));
      });
      el.addEventListener("dblclick", () => {
        const f = c.start + Math.round((w.start * fps - c.in) / c.speed);
        if (f >= c.start && f < clipEnd(c)) store.setPlayhead(f);
      });
      this.wordEls.push(el);
      box.append(el, " ");
    });
    box.addEventListener("keydown", (e) => {
      if ((e.key === "Delete" || e.key === "Backspace") && this.wordSel) {
        e.preventDefault();
        e.stopPropagation();
        void this.removeWords(c, t.words);
      }
      if (e.key === "Escape") this.setSel(null);
      e.stopPropagation();
    });
    body.append(
      h("div", { class: "row gap" }, h("span", { class: "muted grow" }, `${t.words.length} words · dimmed words are outside this clip`), button("Delete selected", () => void this.removeWords(c, t.words), "btn btn-sm", "close", "Ripple-delete the selected words (Delete)")),
      box,
      h("p", { class: "hint" }, "Click or drag to select words, Shift-click to extend, double-click to jump the playhead. Delete removes them from the clip and its linked audio as one undoable edit."),
    );
    if (this.wordSel) this.setSel(this.wordSel[0], this.wordSel[1]);
    this.highlightPlayheadWord();
  }

  private setSel(a: number | null, b?: number): void {
    this.wordSel = a === null ? null : [a, b ?? a];
    this.wordEls.forEach((el, i) => el.classList.toggle("sel", !!this.wordSel && i >= this.wordSel[0] && i <= this.wordSel[1]));
  }

  private highlightPlayheadWord(): void {
    if (!this.open || this.tab !== "transcript" || !this.transcript?.words.length) return;
    const c = this.clip();
    if (!c || c.asset_id !== this.transcriptFor) return;
    const fRel = store.playhead - c.start;
    const t = (c.in + fRel * c.speed) / store.fps;
    const inside = fRel >= 0 && fRel < c.duration;
    this.transcript.words.forEach((w, i) => this.wordEls[i]?.classList.toggle("now", inside && t >= w.start && t < w.end));
  }

  private async removeWords(c: Clip, words: TranscriptWord[]): Promise<void> {
    if (!this.wordSel) return;
    const [a, b] = this.wordSel;
    const ranges: Array<[number, number]> = [[words[a]!.start, words[b]!.end]];
    const r = await this.run("/api/ai/remove-ranges", { timeline: store.doc, clip_id: c.id, ranges }, { label: `Delete ${b - a + 1} word${a === b ? "" : "s"}` });
    if (r) this.setSel(null);
  }

  // ── tools ──

  private card(title: string, desc: string, ...content: Array<Node | null>): HTMLElement {
    const el = h("details", { class: "tool-card" }, h("summary", null, h("strong", null, title), h("span", { class: "muted" }, desc)), h("div", { class: "tool-body" }, content));
    this.tabBody.append(el);
    return el;
  }

  private needClip(types: string[]): Clip | null {
    const c = this.clip();
    if (!c || !types.includes(c.type)) {
      toast(`Select a ${types.join(" or ")} clip first`, "info");
      return null;
    }
    return c;
  }

  private renderTools(): void {
    const c = this.clip();
    this.tabBody.append(h("p", { class: "hint" }, c ? `Selected: ${c.name || c.type}` : "Tip: most tools act on the selected clip."));

    const fillers = h("input", { type: "checkbox", checked: true });
    const minSil = h("input", { class: "input num", type: "number", value: "0.5", step: "0.05", min: "0.1", "aria-label": "Minimum silence (s)" });
    const pad = h("input", { class: "input num", type: "number", value: "0.1", step: "0.05", min: "0", "aria-label": "Padding (s)" });
    this.card(
      "Remove silences & fillers",
      "Ripple-cut pauses and um/uh",
      h("label", { class: "field check" }, fillers, h("span", null, "Also remove filler words")),
      h("div", { class: "grid2" }, h("label", { class: "field" }, h("span", { class: "field-label" }, "Min silence (s)"), minSil), h("label", { class: "field" }, h("span", { class: "field-label" }, "Padding (s)"), pad)),
      button("Remove silences", () => {
        const cl = this.needClip(["video", "audio"]);
        if (cl) void this.run("/api/ai/silence-cut", { timeline: store.doc, clip_id: cl.id, fillers: fillers.checked, min_silence_s: Number(minSil.value), padding_s: Number(pad.value) }, { label: "Remove silences" });
      }, "btn btn-primary btn-sm"),
    );

    const capStyle = h("select", { class: "input", "aria-label": "Caption style" }, h("option", { value: "" }, "Brand default"), h("option", { value: "karaoke" }, "Karaoke highlight"), h("option", { value: "line" }, "Line by line"), h("option", { value: "word" }, "One word at a time"));
    this.card(
      "Auto captions",
      "Transcribe and add a captions track",
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Style"), capStyle),
      button("Generate captions", () => {
        const ids = [...store.selection].filter((id) => {
          const t = findClip(store.doc, id)?.clip.type;
          return t === "video" || t === "audio";
        });
        const style: Record<string, unknown> = {};
        if (capStyle.value) style.mode = capStyle.value;
        if (store.brand?.styles?.captions) Object.assign(style, store.brand.styles.captions);
        void this.run("/api/ai/captions", { timeline: store.doc, ...(ids.length ? { clip_ids: ids } : {}), ...(Object.keys(style).length ? { style } : {}) }, { label: "Auto captions" });
      }, "btn btn-primary btn-sm"),
    );

    const duck = h("input", { class: "input num", type: "number", value: "12", step: "1", min: "1", max: "40", "aria-label": "Duck amount (dB)" });
    this.card(
      "Auto duck music",
      "Lower music under dialogue & VO",
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Duck by (dB)"), duck),
      button("Duck music", () => void this.run("/api/ai/duck", { timeline: store.doc, duck_db: Number(duck.value) }, { label: "Auto duck" }), "btn btn-primary btn-sm"),
    );

    const aspect = h("select", { class: "input", "aria-label": "Target aspect" }, ["9:16", "1:1", "4:5", "16:9"].map((a) => h("option", { value: a }, a)));
    this.card(
      "Auto-reframe",
      "Keep the subject in frame for other aspects",
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Aspect"), aspect),
      h(
        "div",
        { class: "row gap wrap" },
        button("Reframe selected clip", () => {
          const cl = this.needClip(["video", "image"]);
          if (cl) void this.run("/api/ai/reframe", { timeline: store.doc, clip_id: cl.id, aspect: aspect.value }, { label: "Auto-reframe" });
        }, "btn btn-sm"),
        button("Duplicate sequence as…", () => void this.duplicateAs(aspect.value), "btn btn-primary btn-sm", undefined, "Creates a new project at this aspect and reframes every video clip"),
      ),
    );

    const brief = h("textarea", { class: "input", rows: 2, placeholder: "Optional brief, e.g. 'product close-ups over the feature list'", "aria-label": "B-roll brief" });
    this.card("B-roll suggestions", "Find and place cutaways", brief, button("Suggest B-roll", () => void this.run("/api/ai/broll", { timeline: store.doc, ...(brief.value ? { brief: brief.value } : {}) }, { label: "B-roll" }), "btn btn-primary btn-sm"));

    this.card(
      "Scene edit detection",
      "Split the selected clip at shot changes",
      button("Detect scenes", async () => {
        const cl = this.needClip(["video"]);
        if (!cl?.asset_id) return;
        const r = await this.run<{ cuts?: number[] }>("/api/ai/scene-detect", { asset_id: cl.asset_id }, { label: "Scene detection", applyTimeline: false });
        if (!r?.cuts) return;
        const fps = store.fps;
        const frames = r.cuts.map((s) => cl.start + Math.round((s * fps - cl.in) / cl.speed)).filter((f) => f > cl.start && f < clipEnd(cl));
        if (!frames.length) {
          toast("No scene changes inside this clip", "info");
          return;
        }
        store.apply(`Scene edit detection (${frames.length} cuts)`, (tl) => {
          let out = tl;
          const ids = store.linkedSelection ? ops.expandLinked(tl, [cl.id]) : [cl.id];
          for (const f of [...frames].sort((a, b) => b - a)) out = ops.splitAt(out, f, { clipIds: idsAt(out, ids, f) });
          return out;
        });
      }, "btn btn-primary btn-sm"),
    );

    this.card(
      "Enhance speech",
      "Studio-quality dialogue cleanup",
      button("Enhance selected clip's audio", async () => {
        const cl = this.needClip(["audio", "video"]);
        if (!cl?.asset_id) return;
        const assetId = cl.asset_id;
        const r = await this.run<{ asset?: Asset }>("/api/ai/enhance-speech", { asset_id: assetId }, { label: "Enhance speech", applyTimeline: false });
        const a = r?.asset;
        if (!a) return;
        media.addAsset(a);
        // swap the asset on the audio side (the selected audio clip, or the video's linked audio)
        const targets = ops.expandLinked(store.doc, [cl.id]).filter((id) => {
          const x = findClip(store.doc, id)?.clip;
          return x && x.type === "audio" && x.asset_id === assetId;
        });
        if (!targets.length) {
          toast("Enhanced audio saved to the library (no linked audio clip to replace)", "info");
          return;
        }
        store.apply("Enhance speech", (tl) => ops.updateClips(tl, targets, (x) => (x.asset_id = a.id)));
      }, "btn btn-primary btn-sm"),
    );

    const rcBrief = h("textarea", { class: "input", rows: 2, placeholder: "What's the story? e.g. '45s customer testimonial, open on the strongest quote'", "aria-label": "Rough cut brief" });
    const rcAspect = h("select", { class: "input", "aria-label": "Aspect" }, Object.keys(ASPECTS).map((a) => h("option", { value: a }, a)));
    const rcLen = h("input", { class: "input num", type: "number", value: "45", min: "5", step: "5", "aria-label": "Target length (s)" });
    this.card(
      "Rough cut from media",
      "Build a first cut from selected Project media",
      rcBrief,
      h("div", { class: "grid2" }, h("label", { class: "field" }, h("span", { class: "field-label" }, "Aspect"), rcAspect), h("label", { class: "field" }, h("span", { class: "field-label" }, "Target length (s)"), rcLen)),
      button("Create rough cut", async () => {
        let ids = this.getSelectedAssets();
        if (!ids.length) ids = [...media.assets.values()].filter((a) => a.kind === "video").slice(0, 20).map((a) => a.id);
        if (!ids.length) {
          toast("Import some footage first", "info");
          return;
        }
        const r = await this.run<{ project_id?: string }>("/api/ai/rough-cut", { asset_ids: ids, brief: rcBrief.value, aspect: rcAspect.value, target_s: Number(rcLen.value) }, { label: "Rough cut", applyTimeline: false });
        if (r?.project_id) toast("Rough cut ready", "success", 10000, { label: "Open", run: () => openProject(r.project_id!) });
      }, "btn btn-primary btn-sm"),
    );
  }

  private async duplicateAs(aspect: string): Promise<void> {
    const p = store.project;
    if (!p) return;
    const [w, hh] = ASPECTS[aspect] ?? [1080, 1920];
    let tl: Timeline = structuredClone(store.doc);
    tl.width = w;
    tl.height = hh;
    const videoClips = tl.tracks.flatMap((t) => t.clips).filter((c) => c.type === "video" && !c.nest);
    if (this.running) {
      toast("Another AI job is running", "warn");
      return;
    }
    toast(`Reframing ${videoClips.length} clip${videoClips.length === 1 ? "" : "s"} for ${aspect}…`, "info");
    let reframed = 0;
    for (const c of videoClips) {
      try {
        const r = await post<{ job_id?: string; timeline?: Timeline }>("/api/ai/reframe", { timeline: tl, clip_id: c.id, aspect });
        const res = r.job_id ? (await waitJob<{ timeline?: Timeline }>(r.job_id)).result : r;
        if (res?.timeline) {
          tl = normalizeTimeline(res.timeline);
          reframed++;
        }
      } catch (e) {
        if (e instanceof ApiError && e.unavailable) {
          toast("Auto-reframe isn't available on this engine yet — duplicating with center crop.", "warn", 7000);
          break;
        }
        toast(`Reframe failed for ${c.name || c.id}: ${(e as Error).message}`, "error");
      }
    }
    try {
      const created = await post<Project>("/api/projects", { name: `${p.name} (${aspect})`, aspect, fps: tl.fps, data: tl });
      toast(`Created “${created.name}” (${reframed}/${videoClips.length} reframed)`, "success", 10000, { label: "Open", run: () => openProject(created.id) });
    } catch (e) {
      toast(`Couldn't create the ${aspect} sequence: ${(e as Error).message}`, "error");
    }
  }

  // ── chat ──

  private renderChat(): void {
    const log = h("div", { class: "chat-log", "aria-live": "polite" });
    const paint = () => {
      clear(log);
      if (!this.chatLog.length) log.append(h("div", { class: "empty" }, h("p", null, "Describe an edit."), h("p", { class: "muted" }, "“tighten the intro”, “add a lower third for Priya at 0:05”, “make the music quieter under the interview”")));
      for (const m of this.chatLog) log.append(h("div", { class: `chat-msg ${m.who}` }, m.text));
      log.scrollTop = log.scrollHeight;
    };
    paint();
    const input = h("textarea", { class: "input", rows: 2, placeholder: "Ask the editor… (Enter to send)", "aria-label": "Chat command" });
    const send = async () => {
      const msg = input.value.trim();
      if (!msg) return;
      input.value = "";
      this.chatLog.push({ who: "you", text: msg });
      paint();
      await autosave.flush();
      const r = await this.run<{ summary?: string }>("/api/ai/command", { timeline: store.doc, message: msg }, { label: "AI edit" });
      this.chatLog.push({ who: "ai", text: r ? r.summary || "Done — applied as one undoable edit." : "That didn't work (see the notification)." });
      paint();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        void send();
      }
      e.stopPropagation();
    });
    this.tabBody.append(log, h("div", { class: "chat-input" }, input, button("Send", () => void send(), "btn btn-primary")));
  }
}

function idsAt(tl: Timeline, ids: string[], f: number): string[] {
  // after earlier (later-frame) splits the original ids still cover frames before the cut
  const out: string[] = [];
  for (const t of tl.tracks) for (const c of t.clips) if (ids.includes(c.id) && c.start < f && f < clipEnd(c)) out.push(c.id);
  return out;
}
