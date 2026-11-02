// Source monitor (proxy playback, I/O marks, insert/overwrite) and Program
// monitor (realtime canvas preview + exact server frame + transport).

import type { Asset } from "../api";
import { media } from "../media";
import { clipEnd, makeClip, newId, parseTimecode, timecode, type Clip } from "../model";
import * as ops from "../ops";
import { Preview } from "../preview";
import { store } from "../store";
import { h, icon, iconButton, promptDialog, toast } from "../ui";
import { name as assetName } from "./project";
import * as actions from "../actions";

export type MonitorId = "source" | "program";
export let activeMonitor: MonitorId = "program";
export function setActiveMonitor(m: MonitorId): void {
  activeMonitor = m;
  document.querySelectorAll(".monitor").forEach((el) => el.classList.toggle("active", (el as HTMLElement).dataset.monitor === m));
}

export class SourceMonitor {
  readonly el: HTMLElement;
  asset: Asset | null = null;
  private video: HTMLVideoElement;
  private img: HTMLImageElement;
  private title: HTMLElement;
  private tcEl: HTMLElement;
  private durEl: HTMLElement;
  private bar: HTMLElement;
  private barFill: HTMLElement;
  private barIn: HTMLElement;
  private empty: HTMLElement;
  private playBtn: HTMLButtonElement;
  markIn: number | null = null; // seconds
  markOut: number | null = null;

  constructor() {
    this.video = h("video", { class: "source-video", playsinline: true, preload: "auto" });
    this.img = h("img", { class: "source-video", alt: "", style: "display:none" });
    this.title = h("span", { class: "monitor-title ellipsis" }, "Source");
    this.tcEl = h("span", { class: "tc mono" }, "00:00:00:00");
    this.durEl = h("span", { class: "tc mono muted" }, "");
    this.barFill = h("div", { class: "scrub-head" });
    this.barIn = h("div", { class: "scrub-range" });
    this.bar = h("div", { class: "scrub", role: "slider", tabindex: 0, "aria-label": "Source position" }, this.barIn, this.barFill);
    this.empty = h("div", { class: "monitor-empty" }, icon("film", 28), h("p", null, "Double-click a clip in the Project panel to load it here."), h("p", { class: "muted" }, "Mark In (I) and Out (O), then Insert (,) or Overwrite (.) at the playhead on targeted tracks."));
    this.playBtn = iconButton("play", "Play / pause source", () => this.toggle(), "Space");
    const viewport = h("div", { class: "monitor-viewport" }, this.video, this.img, this.empty);
    this.el = h(
      "section",
      { class: "monitor", "data-monitor": "source", "aria-label": "Source monitor" },
      h("div", { class: "monitor-head" }, h("span", { class: "monitor-tag" }, "Source"), this.title),
      viewport,
      this.bar,
      h(
        "div",
        { class: "transport" },
        this.tcEl,
        h(
          "div",
          { class: "transport-btns" },
          iconButton("marker", "Mark in", () => this.mark("in"), "I", "btn icon-btn btn-ghost mark-in"),
          iconButton("stepb", "Step back", () => this.step(-1), "←", "btn icon-btn btn-ghost"),
          this.playBtn,
          iconButton("stepf", "Step forward", () => this.step(1), "→", "btn icon-btn btn-ghost"),
          iconButton("marker", "Mark out", () => this.mark("out"), "O", "btn icon-btn btn-ghost mark-out"),
        ),
        h(
          "div",
          { class: "transport-btns" },
          h("button", { class: "btn btn-sm", type: "button", "data-tip": "Insert into timeline at playhead (,)", onclick: () => this.edit("insert") }, "Insert"),
          h("button", { class: "btn btn-sm", type: "button", "data-tip": "Overwrite into timeline at playhead (.)", onclick: () => this.edit("overwrite") }, "Overwrite"),
        ),
        this.durEl,
      ),
    );
    this.el.addEventListener("pointerdown", () => setActiveMonitor("source"));
    this.video.addEventListener("timeupdate", () => this.sync());
    this.video.addEventListener("seeked", () => this.sync());
    this.video.addEventListener("loadedmetadata", () => this.sync());
    this.video.addEventListener("play", () => this.playBtn.replaceChildren(icon("pause")));
    this.video.addEventListener("pause", () => this.playBtn.replaceChildren(icon("play")));
    const scrubTo = (e: PointerEvent) => {
      const r = this.bar.getBoundingClientRect();
      const d = this.duration();
      if (!d) return;
      this.video.currentTime = Math.max(0, Math.min(d, ((e.clientX - r.left) / r.width) * d));
      this.sync();
    };
    this.bar.addEventListener("pointerdown", (e) => {
      this.bar.setPointerCapture(e.pointerId);
      scrubTo(e);
      const move = (ev: PointerEvent) => scrubTo(ev);
      const up = () => {
        this.bar.removeEventListener("pointermove", move);
        this.bar.removeEventListener("pointerup", up);
      };
      this.bar.addEventListener("pointermove", move);
      this.bar.addEventListener("pointerup", up);
    });
    media.on("media", () => {
      if (this.asset && this.asset.kind !== "image" && !this.video.src.includes("/derived/") && media.status.get(this.asset.id)?.proxy && this.video.paused) {
        const t = this.video.currentTime;
        this.video.src = media.playUrl(this.asset.id);
        this.video.currentTime = t;
      }
      this.sync();
    });
  }

  private fps(): number {
    return store.fps;
  }

  duration(): number {
    if (!this.asset) return 0;
    if (this.asset.kind === "image" || this.asset.kind === "vector") return 5;
    const d = this.video.duration;
    return Number.isFinite(d) && d > 0 ? d : media.duration(this.asset.id) ?? 0;
  }

  open(a: Asset): void {
    this.asset = a;
    this.markIn = null;
    this.markOut = null;
    this.title.textContent = assetName(a);
    this.empty.style.display = "none";
    setActiveMonitor("source");
    if (a.kind === "image" || a.kind === "vector") {
      this.video.pause();
      this.video.removeAttribute("src");
      this.video.style.display = "none";
      this.img.style.display = "";
      this.img.src = media.fileUrl(a.id);
    } else {
      this.img.style.display = "none";
      this.video.style.display = "";
      this.video.src = media.playUrl(a.id);
      void media.ensure(a.id).catch(() => undefined);
    }
    this.sync();
  }

  toggle(): void {
    if (!this.asset || this.video.style.display === "none") return;
    if (this.video.paused) void this.video.play().catch(() => undefined);
    else this.video.pause();
  }

  shuttle(rate: number): void {
    if (!this.asset || this.video.style.display === "none") return;
    if (rate === 0) this.video.pause();
    else if (rate > 0) {
      this.video.playbackRate = rate;
      void this.video.play().catch(() => undefined);
    } else {
      this.video.pause();
      this.video.currentTime = Math.max(0, this.video.currentTime - 1);
    }
  }

  step(n: number): void {
    if (!this.asset) return;
    this.video.pause();
    this.video.currentTime = Math.max(0, Math.min(this.duration(), this.video.currentTime + n / this.fps()));
  }

  home(): void {
    this.video.currentTime = 0;
  }

  end(): void {
    this.video.currentTime = Math.max(0, this.duration() - 1 / this.fps());
  }

  mark(which: "in" | "out"): void {
    if (!this.asset) return;
    const t = this.asset.kind === "image" ? 0 : this.video.currentTime;
    if (which === "in") {
      this.markIn = t;
      if (this.markOut !== null && this.markOut <= t) this.markOut = null;
    } else {
      this.markOut = t + 1 / this.fps();
      if (this.markIn !== null && this.markIn >= this.markOut) this.markIn = null;
    }
    this.sync();
  }

  private sync(): void {
    const d = this.duration();
    const t = this.asset?.kind === "image" ? 0 : this.video.currentTime || 0;
    this.tcEl.textContent = timecode(Math.round(t * this.fps()), this.fps());
    this.durEl.textContent = d ? timecode(Math.round(this.span()[1] * this.fps() - this.span()[0] * this.fps()), this.fps()) : "";
    this.barFill.style.left = d ? `${(t / d) * 100}%` : "0";
    const [i, o] = this.span();
    this.barIn.style.left = d ? `${(i / d) * 100}%` : "0";
    this.barIn.style.width = d ? `${((o - i) / d) * 100}%` : "0";
    this.barIn.style.display = this.markIn !== null || this.markOut !== null ? "" : "none";
  }

  /** [in, out) in source seconds. */
  private span(): [number, number] {
    const d = this.duration();
    return [this.markIn ?? 0, this.markOut ?? d];
  }

  edit(mode: "insert" | "overwrite"): void {
    const a = this.asset;
    if (!a) {
      toast("Load a clip in the Source monitor first (double-click in the Project panel)", "info");
      return;
    }
    if (store.readOnly) {
      toast(store.readOnlyReason || "Read-only", "warn");
      return;
    }
    if (a.kind === "vector") {
      toast("SVG artwork must be rasterized before it can go on the timeline", "warn");
      return;
    }
    const fps = this.fps();
    const [i, o] = this.span();
    const inF = Math.round(i * fps);
    const len = Math.max(1, Math.round(o * fps) - inF);
    const at = store.markIn ?? store.playhead;
    const targets = store.doc.tracks.filter((t) => store.targets.has(t.id) && !t.locked);
    const vTrack = targets.find((t) => t.kind === "video") ?? (store.targets.size ? undefined : store.doc.tracks.find((t) => t.kind === "video" && !t.locked));
    const aTrack = targets.find((t) => t.kind === "audio") ?? (store.targets.size ? undefined : store.doc.tracks.find((t) => t.kind === "audio" && !t.locked));
    const probe = media.status.get(a.id)?.probe;
    const hasAudio = a.kind === "audio" || (a.kind === "video" && probe?.has_audio !== false);
    const placements: Array<{ trackId: string; clip: Clip }> = [];
    const link = newId("lk");
    const nm = assetName(a);
    if ((a.kind === "video" || a.kind === "image") && vTrack) {
      placements.push({ trackId: vTrack.id, clip: makeClip({ type: a.kind === "image" ? "image" : "video", asset_id: a.id, name: nm, start: at, duration: len, in: a.kind === "image" ? 0 : inF, linked: a.kind === "video" && hasAudio && aTrack ? link : null }) });
    }
    if (hasAudio && aTrack && a.kind !== "image") {
      placements.push({ trackId: aTrack.id, clip: makeClip({ type: "audio", asset_id: a.id, name: nm, start: at, duration: len, in: inF, linked: a.kind === "video" && vTrack ? link : null, role: a.kind === "audio" ? "music" : "dialogue" }) });
    }
    if (!placements.length) {
      toast("No targeted track for this media. Click a track's V/A target toggle in the timeline header.", "warn", 6000);
      return;
    }
    store.apply(mode === "insert" ? "Insert edit" : "Overwrite edit", (tl) => (mode === "insert" ? ops.insert(tl, placements, at) : ops.overwrite(tl, placements)));
    store.select(placements.map((p) => p.clip.id));
    store.setPlayhead(at + len);
    if (store.markIn !== null) store.setMarks(null, null);
  }
}

export class ProgramMonitor {
  readonly el: HTMLElement;
  readonly preview: Preview;
  private tcEl: HTMLButtonElement;
  private durEl: HTMLElement;
  private exactBadge: HTMLElement;
  private proxyBadge: HTMLElement;
  private playBtn: HTMLButtonElement;
  private viewport: HTMLElement;
  private safe: HTMLElement;

  constructor() {
    const canvas = h("canvas", { class: "program-canvas", "aria-label": "Program monitor" });
    const exact = h("img", { class: "program-exact", alt: "Exact server-rendered frame", style: "display:none" });
    this.exactBadge = h("span", { class: "badge badge-exact", style: "display:none" }, "Exact frame");
    this.proxyBadge = h("span", { class: "badge badge-warn", style: "display:none" }, "Building proxies…");
    this.safe = h("div", { class: "safe-guides", style: "display:none" });
    const stage = h("div", { class: "program-stage" }, canvas, exact, this.safe);
    this.viewport = h("div", { class: "monitor-viewport" }, stage, h("div", { class: "badges" }, this.exactBadge, this.proxyBadge));
    this.preview = new Preview(canvas, exact);
    this.tcEl = h("button", { class: "tc mono tc-btn", type: "button", "data-tip": "Click to go to a timecode" }, "00:00:00:00");
    this.durEl = h("span", { class: "tc mono muted" }, "");
    this.playBtn = iconButton("play", "Play / pause", () => this.preview.toggle(), "Space");
    this.tcEl.addEventListener("click", async () => {
      const v = await promptDialog("Go to timecode", "Timecode (HH:MM:SS:FF, seconds like 12.5s, or frames)", timecode(store.playhead, store.fps), "Go");
      if (v === null) return;
      const f = parseTimecode(v, store.fps);
      if (f !== null) store.setPlayhead(f);
    });
    this.el = h(
      "section",
      { class: "monitor active", "data-monitor": "program", "aria-label": "Program monitor" },
      h("div", { class: "monitor-head" }, h("span", { class: "monitor-tag tag-program" }, "Program"), h("span", { class: "monitor-title" }, "Sequence"), h("span", { class: "grow" }), iconButton("grid", "Toggle safe margins", () => this.toggleSafe(), undefined, "btn icon-btn btn-ghost")),
      this.viewport,
      h(
        "div",
        { class: "transport" },
        this.tcEl,
        h(
          "div",
          { class: "transport-btns" },
          iconButton("marker", "Mark in", () => actions.markIn(), "I", "btn icon-btn btn-ghost mark-in"),
          iconButton("chevl", "Previous edit", () => actions.jumpEdit(-1), "↑", "btn icon-btn btn-ghost"),
          iconButton("stepb", "Step back", () => store.setPlayhead(store.playhead - 1), "←", "btn icon-btn btn-ghost"),
          this.playBtn,
          iconButton("stepf", "Step forward", () => store.setPlayhead(store.playhead + 1), "→", "btn icon-btn btn-ghost"),
          iconButton("chevr", "Next edit", () => actions.jumpEdit(1), "↓", "btn icon-btn btn-ghost"),
          iconButton("marker", "Mark out", () => actions.markOut(), "O", "btn icon-btn btn-ghost mark-out"),
        ),
        h("div", { class: "transport-btns" }, iconButton("marker", "Add marker", () => actions.addMarker(), "M", "btn icon-btn btn-ghost")),
        this.durEl,
      ),
    );
    this.el.addEventListener("pointerdown", () => setActiveMonitor("program"));
    new ResizeObserver(() => this.fit()).observe(this.viewport);
    this.preview.onExact = (s) => {
      this.exactBadge.style.display = s === "shown" || s === "loading" ? "" : "none";
      this.exactBadge.textContent = s === "loading" ? "Rendering exact frame…" : "Exact frame";
      this.exactBadge.classList.toggle("loading", s === "loading");
    };
    this.preview.onPlayState = () => this.playBtn.replaceChildren(icon(this.preview.playing ? "pause" : "play"));
    store.on("playhead", () => {
      this.syncTc();
      if (!this.preview.playing) this.preview.invalidate();
    });
    store.on("doc", () => {
      this.syncTc();
      this.preview.invalidate();
    });
    store.on("project", () => this.fit());
    media.on("media", () => {
      const n = media.building.size;
      this.proxyBadge.style.display = n ? "" : "none";
      this.proxyBadge.textContent = `Building proxies (${n})…`;
    });
  }

  private toggleSafe(): void {
    this.safe.style.display = this.safe.style.display === "none" ? "" : "none";
  }

  fit(): void {
    const r = this.viewport.getBoundingClientRect();
    this.preview.resize(Math.max(32, r.width - 8), Math.max(18, r.height - 8));
    const c = this.preview.canvas;
    this.safe.style.width = c.style.width;
    this.safe.style.height = c.style.height;
  }

  private syncTc(): void {
    this.tcEl.textContent = timecode(store.playhead, store.fps);
    this.durEl.textContent = timecode(store.duration, store.fps);
  }
}

/** Clips on the timeline at the playhead that belong to the given asset (for Source ↔ Program matching). */
export function clipsForAsset(assetId: string): Clip[] {
  const out: Clip[] = [];
  for (const t of store.doc.tracks) for (const c of t.clips) if (c.asset_id === assetId && c.start <= store.playhead && store.playhead < clipEnd(c)) out.push(c);
  return out;
}
