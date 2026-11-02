// Canvas timeline: ruler, track headers, clips (thumbnails / waveforms /
// keyframes / transitions), markers, playhead, and all pointer editing
// (select, marquee, move with snapping, trim, roll, razor, scrub).

import { razorAt, sourceCtx } from "./actions";
import { media } from "./media";
import { clipEnd, findClip, timecode, type Clip, type Timeline, type Track } from "./model";
import * as ops from "./ops";
import type { Preview } from "./preview";
import { store } from "./store";
import { promptDialog } from "./ui";

const C = {
  bg: "#0B0D12",
  panel: "#12151C",
  panel2: "#181C25",
  border: "#232836",
  text: "#E6E8EE",
  muted: "#8A90A2",
  ember: "#FF6B35",
  sel: "rgba(255,107,53,0.30)",
  comment: "#4EA8FF",
};

const CLIP_COLORS: Record<string, string> = {
  video: "#2F5D8C",
  image: "#2F6F6A",
  text: "#7C4DA8",
  shape: "#5E55A0",
  color: "#545B6B",
  captions: "#A0702A",
  graphic: "#B0477A",
  adjustment: "#47566E",
  audio: "#2E7650",
};

const HEADER_W = 172;
const RULER_H = 30;
const SCROLL_H = 10;
const GAP = 6;
const EDGE_PX = 6;
const SNAP_PX = 8;
const VIDEO_H = 46;
const AUDIO_H = 40;

interface Row {
  track: Track;
  y: number;
  h: number;
}

type Hit =
  | { kind: "ruler" }
  | { kind: "marker"; index: number }
  | { kind: "comment"; index: number }
  | { kind: "header"; track: Track; button: "lock" | "eye" | "mute" | "solo" | "target" | null }
  | { kind: "clip"; clip: Clip; track: Track; edge: "start" | "end" | null }
  | { kind: "hscroll" }
  | { kind: "empty"; track: Track | null };

type Drag =
  | { mode: "scrub" }
  | { mode: "hscroll"; x0: number; scroll0: number }
  | { mode: "marquee"; x0: number; y0: number; x1: number; y1: number; additive: boolean; base: Set<string> }
  | { mode: "marker"; index: number; x0: number; f0: number; moved: boolean }
  | { mode: "move"; x0: number; y0: number; ids: string[]; anchor: Clip; anchorTrack: Track; orig: Timeline; moved: boolean }
  | { mode: "trim"; x0: number; ids: string[]; edge: "start" | "end"; anchor: Clip; orig: Timeline; moved: boolean }
  | { mode: "roll"; x0: number; pairs: Array<[string, string]>; anchorEnd: number; orig: Timeline; moved: boolean };

export interface CommentMarker {
  f: number;
  out?: number | null;
  label: string;
  resolved?: boolean;
  id: string;
}

export class TimelineView {
  readonly el: HTMLElement;
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private cssW = 800;
  private cssH = 300;
  ppf = 4; // pixels per frame
  scrollX = 0; // px
  scrollY = 0;
  private rows: Row[] = [];
  private contentH = 0;
  private drag: Drag | null = null;
  private dirty = true;
  private snapLine: number | null = null;
  private hoverCursor = "default";
  commentMarkers: CommentMarker[] = [];
  onCommentClick: (id: string) => void = () => {};
  private preview: Preview;
  private altDown = false;
  private scrolledInit = false;

  constructor(host: HTMLElement, preview: Preview) {
    this.preview = preview;
    this.el = host;
    this.canvas = document.createElement("canvas");
    this.canvas.className = "timeline-canvas";
    this.canvas.tabIndex = 0;
    this.canvas.setAttribute("role", "application");
    this.canvas.setAttribute("aria-label", "Timeline. Use J K L to shuttle, arrows to step, C razor, V select.");
    host.append(this.canvas);
    this.ctx = this.canvas.getContext("2d")!;
    new ResizeObserver(() => this.resize()).observe(host);
    this.canvas.addEventListener("pointerdown", this.onDown);
    this.canvas.addEventListener("pointermove", this.onMove);
    this.canvas.addEventListener("pointerup", this.onUp);
    this.canvas.addEventListener("pointercancel", this.onUp);
    this.canvas.addEventListener("dblclick", this.onDbl);
    this.canvas.addEventListener("wheel", this.onWheel, { passive: false });
    this.canvas.addEventListener("pointerleave", () => this.canvas.classList.remove("hovering"));
    window.addEventListener("keydown", (e) => (this.altDown = e.altKey));
    window.addEventListener("keyup", (e) => (this.altDown = e.altKey));
    for (const ev of ["doc", "selection", "playhead", "tool", "marks", "readonly"] as const) store.on(ev, () => this.invalidate());
    store.on("playhead", () => this.follow());
    media.on("media", () => this.invalidate());
    const loop = () => {
      if (this.dirty) {
        this.dirty = false;
        this.draw();
      }
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  invalidate(): void {
    this.dirty = true;
  }

  private resize(): void {
    const r = this.el.getBoundingClientRect();
    this.cssW = Math.max(200, r.width);
    this.cssH = Math.max(120, r.height);
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(this.cssW * dpr);
    this.canvas.height = Math.round(this.cssH * dpr);
    this.canvas.style.width = `${this.cssW}px`;
    this.canvas.style.height = `${this.cssH}px`;
    this.invalidate();
  }

  // ── geometry ──

  private get laneW(): number {
    return this.cssW - HEADER_W;
  }
  xOf(f: number): number {
    return HEADER_W + f * this.ppf - this.scrollX;
  }
  fOf(x: number): number {
    return (x - HEADER_W + this.scrollX) / this.ppf;
  }

  private layout(tl: Timeline): void {
    const rows: Row[] = [];
    let y = RULER_H + 4 - this.scrollY;
    const vids = tl.tracks.filter((t) => t.kind === "video").reverse();
    const auds = tl.tracks.filter((t) => t.kind === "audio");
    for (const t of vids) {
      rows.push({ track: t, y, h: VIDEO_H });
      y += VIDEO_H + 2;
    }
    const divider = y + this.scrollY;
    y += GAP;
    for (const t of auds) {
      rows.push({ track: t, y, h: AUDIO_H });
      y += AUDIO_H + 2;
    }
    this.rows = rows;
    this.contentH = y + this.scrollY - RULER_H + 12;
    if (!this.scrolledInit && this.cssH > 150) {
      // start with V1 and the audio tracks in view (video stacks grow upwards)
      this.scrolledInit = true;
      const visible = this.cssH - RULER_H - SCROLL_H;
      const want = Math.max(0, Math.min(this.contentH - visible, divider - RULER_H - visible * 0.55));
      if (want > 0) {
        this.scrollY = want;
        this.layout(tl);
      }
    }
  }

  /** Timeline frame + track under a client point (for drag-and-drop from the Project panel). */
  locate(clientX: number, clientY: number): { f: number; track: Track | null } | null {
    const r = this.canvas.getBoundingClientRect();
    const x = clientX - r.left;
    const y = clientY - r.top;
    if (x < HEADER_W || y < RULER_H) return null;
    return { f: Math.max(0, Math.round(this.fOf(x))), track: this.rowAt(y)?.track ?? null };
  }

  get element(): HTMLCanvasElement {
    return this.canvas;
  }

  private rowAt(y: number): Row | null {
    return this.rows.find((r) => y >= r.y && y < r.y + r.h) ?? null;
  }

  private totalFrames(): number {
    return Math.max(store.duration + store.fps * 10, store.fps * 30);
  }

  // ── zoom / scroll ──

  setZoom(ppf: number, anchorX?: number): void {
    const ax = anchorX ?? Math.min(Math.max(this.xOf(store.playhead), HEADER_W), this.cssW);
    const f = this.fOf(ax);
    this.ppf = Math.max(0.02, Math.min(48, ppf));
    this.scrollX = Math.max(0, f * this.ppf - (ax - HEADER_W));
    this.invalidate();
  }
  zoomIn(): void {
    this.setZoom(this.ppf * 1.5);
  }
  zoomOut(): void {
    this.setZoom(this.ppf / 1.5);
  }
  zoomFit(): void {
    const d = Math.max(store.duration, store.fps * 5);
    this.ppf = Math.max(0.02, (this.laneW - 40) / d);
    this.scrollX = 0;
    this.invalidate();
  }

  /** Keep the playhead visible (page-scroll while playing). */
  private follow(): void {
    const x = this.xOf(store.playhead);
    if (this.drag) return;
    if (x > this.cssW - 20) this.scrollX += x - HEADER_W - 40;
    else if (x < HEADER_W) this.scrollX = Math.max(0, store.playhead * this.ppf - 40);
  }

  // ── drawing ──

  private draw(): void {
    const ctx = this.ctx;
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const tl = store.view;
    this.layout(tl);
    const W = this.cssW;
    const H = this.cssH;
    ctx.fillStyle = C.bg;
    ctx.fillRect(0, 0, W, H);

    // lanes
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, RULER_H, W, H - RULER_H - SCROLL_H);
    ctx.clip();
    for (const r of this.rows) {
      ctx.fillStyle = r.track.kind === "video" ? "#10131A" : "#0F1218";
      ctx.fillRect(HEADER_W, r.y, W - HEADER_W, r.h);
      if (r.track.locked) {
        ctx.fillStyle = "rgba(255,255,255,0.025)";
        for (let x = HEADER_W - (this.scrollX % 12); x < W; x += 12) ctx.fillRect(x, r.y, 6, r.h);
      }
    }
    // in/out range shading
    if (store.markIn !== null || store.markOut !== null) {
      const a = this.xOf(store.markIn ?? 0);
      const b = this.xOf(store.markOut ?? this.totalFrames());
      ctx.fillStyle = "rgba(255,255,255,0.04)";
      ctx.fillRect(Math.max(HEADER_W, a), RULER_H, Math.max(0, b - Math.max(HEADER_W, a)), H);
    }
    const lo = this.fOf(HEADER_W) - 1;
    const hi = this.fOf(W) + 1;
    for (const r of this.rows) {
      if (r.y > H || r.y + r.h < RULER_H) continue;
      for (const c of r.track.clips) {
        if (clipEnd(c) < lo || c.start > hi) continue;
        this.drawClip(ctx, tl, r, c);
      }
    }
    // marquee
    if (this.drag?.mode === "marquee") {
      const d = this.drag;
      ctx.fillStyle = "rgba(255,107,53,0.10)";
      ctx.strokeStyle = C.ember;
      ctx.lineWidth = 1;
      const x = Math.min(d.x0, d.x1);
      const y = Math.min(d.y0, d.y1);
      ctx.fillRect(x, y, Math.abs(d.x1 - d.x0), Math.abs(d.y1 - d.y0));
      ctx.strokeRect(x + 0.5, y + 0.5, Math.abs(d.x1 - d.x0), Math.abs(d.y1 - d.y0));
    }
    ctx.restore();

    this.drawHeaders(ctx, tl);
    this.drawRuler(ctx, tl);
    this.drawScrollbar(ctx);

    // snap line
    if (this.snapLine !== null) {
      const x = Math.round(this.xOf(this.snapLine)) + 0.5;
      ctx.strokeStyle = "#FFFFFF";
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x, RULER_H);
      ctx.lineTo(x, H);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    // playhead
    const px = Math.round(this.xOf(store.playhead)) + 0.5;
    if (px >= HEADER_W) {
      ctx.strokeStyle = C.ember;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(px, RULER_H - 8);
      ctx.lineTo(px, H - SCROLL_H);
      ctx.stroke();
      ctx.fillStyle = C.ember;
      ctx.beginPath();
      ctx.moveTo(px - 6, RULER_H - 14);
      ctx.lineTo(px + 6, RULER_H - 14);
      ctx.lineTo(px + 6, RULER_H - 7);
      ctx.lineTo(px, RULER_H - 1);
      ctx.lineTo(px - 6, RULER_H - 7);
      ctx.closePath();
      ctx.fill();
    }
  }

  private drawClip(ctx: CanvasRenderingContext2D, tl: Timeline, r: Row, c: Clip): void {
    const x = this.xOf(c.start);
    const w = Math.max(1, c.duration * this.ppf);
    const y = r.y + 1;
    const h = r.h - 2;
    const selected = store.selection.has(c.id);
    const base = CLIP_COLORS[c.type] ?? "#555";
    ctx.save();
    ctx.globalAlpha = c.enabled ? 1 : 0.4;
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, 3);
    ctx.fillStyle = base;
    ctx.fill();
    ctx.clip();
    // body content
    const bodyY = y + 15;
    const bodyH = h - 16;
    if (c.type === "video" && c.asset_id) this.drawThumbs(ctx, tl, c, x, bodyY, w, bodyH);
    else if (c.type === "image" && c.asset_id) {
      const img = media.image(c.asset_id);
      if (img.complete && img.naturalWidth) {
        const tw = (bodyH * img.naturalWidth) / img.naturalHeight;
        const x0 = Math.max(x, HEADER_W - tw);
        for (let xx = x0; xx < Math.min(x + w, this.cssW); xx += tw) ctx.drawImage(img, xx, bodyY, tw, bodyH);
      }
    } else if (c.type === "color") {
      ctx.fillStyle = c.color;
      ctx.fillRect(x, bodyY, w, bodyH);
    } else if (c.type === "audio" && c.asset_id) this.drawWave(ctx, tl, c, r.track, x, bodyY, w, bodyH);
    else if (c.type === "text" || c.type === "captions" || c.type === "graphic") {
      ctx.fillStyle = "rgba(255,255,255,0.75)";
      ctx.font = "11px Inter, sans-serif";
      const t =
        c.type === "text"
          ? c.text
          : c.type === "graphic"
            ? (c.graphic?.controls.filter((k) => k.kind === "text").map((k) => String(k.value ?? "")) ?? []).concat(c.graphic?.controls.length ? [] : (c.graphic?.layers ?? []).filter((l) => l.type === "text").map((l) => l.text)).filter(Boolean).join(" · ")
            : (c.captions?.words ?? []).map((wd) => wd.w).join(" ");
      ctx.fillText(t.replace(/\n/g, " ").slice(0, 200), Math.max(x, HEADER_W) + 6, bodyY + bodyH / 2 + 4);
    }
    // header band
    ctx.fillStyle = "rgba(0,0,0,0.35)";
    ctx.fillRect(x, y, w, 14);
    // transition marker
    if (c.transition_in && c.transition_in.duration > 0) {
      const tw = Math.min(w, c.transition_in.duration * this.ppf);
      const g = ctx.createLinearGradient(x, 0, x + tw, 0);
      g.addColorStop(0, "rgba(255,255,255,0.55)");
      g.addColorStop(1, "rgba(255,255,255,0.05)");
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.moveTo(x, y + h);
      ctx.lineTo(x + tw, y);
      ctx.lineTo(x, y);
      ctx.closePath();
      ctx.fill();
    }
    // anim in/out ramps
    ctx.strokeStyle = "rgba(255,255,255,0.6)";
    ctx.lineWidth = 1;
    if (c.anim_in && c.anim_in.duration > 0) {
      const aw = Math.min(w, c.anim_in.duration * this.ppf);
      ctx.beginPath();
      ctx.moveTo(x, y + 14);
      ctx.lineTo(x + aw, y + 2);
      ctx.stroke();
    }
    if (c.anim_out && c.anim_out.duration > 0) {
      const aw = Math.min(w, c.anim_out.duration * this.ppf);
      ctx.beginPath();
      ctx.moveTo(x + w - aw, y + 2);
      ctx.lineTo(x + w, y + 14);
      ctx.stroke();
    }
    // name
    const nameX = Math.max(x, HEADER_W) + 5;
    ctx.fillStyle = C.text;
    ctx.font = "600 10.5px Inter, sans-serif";
    let label = c.name || defaultName(c);
    if (c.speed !== 1) label += ` (${Math.round(c.speed * 100)}%)`;
    if (c.adjust && c.type !== "adjustment") label = `fx ${label}`;
    if (c.linked) label = `${label}`;
    ctx.fillText(label, nameX, y + 10.5, Math.max(0, x + w - nameX - 4));
    // keyframe ticks
    const kfFrames = new Set<number>();
    for (const list of Object.values(c.keyframes)) for (const k of list) if (k.f >= 0 && k.f < c.duration) kfFrames.add(k.f);
    ctx.fillStyle = "#FFD3BF";
    for (const f of kfFrames) {
      const kx = this.xOf(c.start + f);
      const ky = y + h - 5;
      ctx.beginPath();
      ctx.moveTo(kx, ky - 3.5);
      ctx.lineTo(kx + 3.5, ky);
      ctx.lineTo(kx, ky + 3.5);
      ctx.lineTo(kx - 3.5, ky);
      ctx.closePath();
      ctx.fill();
    }
    ctx.restore();
    // borders
    ctx.lineWidth = selected ? 2 : 1;
    ctx.strokeStyle = selected ? C.ember : "rgba(0,0,0,0.55)";
    ctx.beginPath();
    ctx.roundRect(x + 0.5, y + 0.5, w - 1, h - 1, 3);
    ctx.stroke();
    if (selected) {
      ctx.fillStyle = C.sel;
      ctx.beginPath();
      ctx.roundRect(x, y, w, h, 3);
      ctx.fill();
    }
    if (c.linked && w > 30) {
      ctx.fillStyle = "rgba(255,255,255,0.5)";
      ctx.fillRect(x + w - 8, y + 4, 4, 4);
    }
  }

  private drawThumbs(ctx: CanvasRenderingContext2D, tl: Timeline, c: Clip, x: number, y: number, w: number, h: number): void {
    const id = c.asset_id!;
    const meta = media.status.get(id)?.thumbs_meta;
    const img = media.thumbs.get(id);
    if (!meta || !img || !img.complete || !img.naturalWidth) {
      void media.ensure(id).catch(() => undefined);
      return;
    }
    const tw = (meta.tile_w * h) / meta.tile_h;
    const left = Math.max(x, HEADER_W - tw);
    const right = Math.min(x + w, this.cssW);
    for (let xx = x + Math.floor((left - x) / tw) * tw; xx < right; xx += tw) {
      const fRel = (xx - x) / this.ppf;
      const t = (c.in + fRel * c.speed) / tl.fps;
      const idx = Math.max(0, Math.min(meta.tiles - 1, Math.floor(t / meta.every_s)));
      ctx.drawImage(img, idx * meta.tile_w, 0, meta.tile_w, meta.tile_h, xx, y, tw, h);
    }
  }

  private drawWave(ctx: CanvasRenderingContext2D, tl: Timeline, c: Clip, track: Track, x: number, y: number, w: number, h: number): void {
    const id = c.asset_id!;
    const p = media.peaks.get(id);
    if (!p || !p.peaks.length) {
      void media.ensure(id).catch(() => undefined);
      return;
    }
    const left = Math.max(x, HEADER_W);
    const right = Math.min(x + w, this.cssW);
    const mid = y + h / 2;
    const gain = Math.min(2, c.volume * (track.muted ? 0.3 : 1));
    ctx.fillStyle = "rgba(170,240,200,0.75)";
    for (let px = Math.floor(left); px < right; px++) {
      const f0 = (px - x) / this.ppf;
      const f1 = (px + 1 - x) / this.ppf;
      const i0 = Math.floor(((c.in + f0 * c.speed) / tl.fps) * p.per_second);
      const i1 = Math.max(i0 + 1, Math.floor(((c.in + f1 * c.speed) / tl.fps) * p.per_second));
      let m = 0;
      for (let i = i0; i < i1 && i < p.peaks.length; i++) m = Math.max(m, p.peaks[i] ?? 0);
      const a = Math.min(1, m * gain) * (h / 2 - 1);
      if (a > 0.3) ctx.fillRect(px, mid - a, 1, a * 2);
    }
  }

  private drawHeaders(ctx: CanvasRenderingContext2D, tl: Timeline): void {
    const H = this.cssH;
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, RULER_H, HEADER_W, H - RULER_H);
    ctx.clip();
    ctx.fillStyle = C.panel;
    ctx.fillRect(0, RULER_H, HEADER_W, H - RULER_H);
    for (const r of this.rows) {
      const t = r.track;
      ctx.fillStyle = C.panel2;
      ctx.fillRect(0, r.y, HEADER_W - 1, r.h);
      // target toggle
      const targeted = store.targets.has(t.id);
      ctx.fillStyle = targeted ? C.ember : "#2A3040";
      ctx.beginPath();
      ctx.roundRect(6, r.y + r.h / 2 - 10, 28, 20, 3);
      ctx.fill();
      ctx.fillStyle = targeted ? "#0B0D12" : C.muted;
      ctx.font = "700 10px Inter, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(t.name || t.id, 20, r.y + r.h / 2 + 3.5, 26);
      ctx.textAlign = "start";
      for (const b of this.headerButtons(t)) {
        const on = b.on;
        ctx.fillStyle = on ? b.color : "#232836";
        ctx.beginPath();
        ctx.roundRect(b.x, r.y + r.h / 2 - 9, 22, 18, 3);
        ctx.fill();
        ctx.fillStyle = on ? "#0B0D12" : C.muted;
        ctx.font = "700 9.5px Inter, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(b.glyph, b.x + 11, r.y + r.h / 2 + 3.5);
        ctx.textAlign = "start";
      }
      if (t.kind === "audio" && t.volume !== 1) {
        ctx.fillStyle = C.muted;
        ctx.font = "9px 'JetBrains Mono', monospace";
        ctx.fillText(`${(20 * Math.log10(Math.max(1e-4, t.volume))).toFixed(1)}dB`, 40, r.y + r.h - 5);
      }
    }
    ctx.restore();
    ctx.strokeStyle = C.border;
    ctx.beginPath();
    ctx.moveTo(HEADER_W - 0.5, RULER_H);
    ctx.lineTo(HEADER_W - 0.5, H);
    ctx.stroke();
    void tl;
  }

  private headerButtons(t: Track): Array<{ id: "lock" | "eye" | "mute" | "solo"; x: number; glyph: string; on: boolean; color: string }> {
    const out: Array<{ id: "lock" | "eye" | "mute" | "solo"; x: number; glyph: string; on: boolean; color: string }> = [];
    let x = HEADER_W - 28;
    out.push({ id: "lock", x, glyph: "L", on: t.locked, color: "#C9CED9" });
    x -= 26;
    if (t.kind === "video") out.push({ id: "eye", x, glyph: t.hidden ? "—" : "◉", on: t.hidden, color: "#C9CED9" });
    else {
      out.push({ id: "solo", x, glyph: "S", on: t.solo, color: "#F2C14E" });
      x -= 26;
      out.push({ id: "mute", x, glyph: "M", on: t.muted, color: "#4EA8FF" });
    }
    return out;
  }

  private drawRuler(ctx: CanvasRenderingContext2D, tl: Timeline): void {
    const W = this.cssW;
    ctx.fillStyle = C.panel;
    ctx.fillRect(0, 0, W, RULER_H);
    ctx.strokeStyle = C.border;
    ctx.beginPath();
    ctx.moveTo(0, RULER_H - 0.5);
    ctx.lineTo(W, RULER_H - 0.5);
    ctx.stroke();
    // timecode at top-left
    ctx.fillStyle = C.ember;
    ctx.font = "600 13px 'JetBrains Mono', monospace";
    ctx.fillText(timecode(store.playhead, tl.fps), 10, 19);
    ctx.save();
    ctx.beginPath();
    ctx.rect(HEADER_W, 0, W - HEADER_W, RULER_H);
    ctx.clip();
    const fps = tl.fps;
    const steps = [1, 2, 5, 10, fps / 2, fps, fps * 2, fps * 5, fps * 10, fps * 15, fps * 30, fps * 60, fps * 120, fps * 300, fps * 600, fps * 1800].filter((s) => Number.isInteger(s) && s > 0);
    const major = steps.find((s) => s * this.ppf >= 90) ?? steps[steps.length - 1]!;
    const minorCand = steps.filter((s) => major % s === 0 && s < major && s * this.ppf >= 8);
    const minor = minorCand[minorCand.length - 1] ?? major;
    const f0 = Math.max(0, Math.floor(this.fOf(HEADER_W) / minor) * minor);
    const f1 = this.fOf(W);
    ctx.strokeStyle = "#3A4052";
    ctx.fillStyle = C.muted;
    ctx.font = "10px 'JetBrains Mono', monospace";
    ctx.beginPath();
    for (let f = f0; f <= f1; f += minor) {
      const x = Math.round(this.xOf(f)) + 0.5;
      const isMajor = f % major === 0;
      ctx.moveTo(x, isMajor ? RULER_H - 14 : RULER_H - 6);
      ctx.lineTo(x, RULER_H);
      if (isMajor) ctx.fillText(timecode(f, fps), x + 3, 11);
    }
    ctx.stroke();
    // in/out marks
    for (const [m, ch] of [
      [store.markIn, "{"],
      [store.markOut, "}"],
    ] as const) {
      if (m === null) continue;
      const x = this.xOf(m);
      ctx.fillStyle = "#E6E8EE";
      ctx.font = "700 13px Inter, sans-serif";
      ctx.fillText(ch, x - (ch === "{" ? 1 : 6), RULER_H - 4);
    }
    // markers
    tl.markers.forEach((m) => {
      const x = this.xOf(m.f);
      ctx.fillStyle = m.color || C.ember;
      ctx.beginPath();
      ctx.moveTo(x - 5, 13);
      ctx.lineTo(x + 5, 13);
      ctx.lineTo(x + 5, 20);
      ctx.lineTo(x, 25);
      ctx.lineTo(x - 5, 20);
      ctx.closePath();
      ctx.fill();
      if (m.duration > 0) ctx.fillRect(x, 23, m.duration * this.ppf, 2);
      if (m.label) {
        ctx.fillStyle = C.text;
        ctx.font = "10px Inter, sans-serif";
        ctx.fillText(m.label, x + 7, 22, 140);
      }
    });
    for (const cm of this.commentMarkers) {
      const x = this.xOf(cm.f);
      ctx.fillStyle = cm.resolved ? "#3A5B7A" : C.comment;
      if (cm.out != null && cm.out > cm.f) ctx.fillRect(x, RULER_H - 4, (cm.out - cm.f) * this.ppf, 3);
      ctx.beginPath();
      ctx.arc(x, RULER_H - 9, 4.5, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  private drawScrollbar(ctx: CanvasRenderingContext2D): void {
    const visH = this.cssH - RULER_H - SCROLL_H;
    if (this.contentH > visH + 1) {
      const th = Math.max(24, (visH / this.contentH) * visH);
      const ty = RULER_H + (this.scrollY / Math.max(1, this.contentH - visH)) * (visH - th);
      ctx.fillStyle = "#2E3546";
      ctx.beginPath();
      ctx.roundRect(this.cssW - 6, ty, 4, th, 2);
      ctx.fill();
    }
    const y = this.cssH - SCROLL_H;
    ctx.fillStyle = C.panel;
    ctx.fillRect(HEADER_W, y, this.laneW, SCROLL_H);
    const total = this.totalFrames() * this.ppf;
    const vis = this.laneW;
    const tw = Math.max(30, (vis / Math.max(vis, total)) * vis);
    const tx = HEADER_W + (this.scrollX / Math.max(1, total - vis)) * (vis - tw);
    ctx.fillStyle = "#2E3546";
    ctx.beginPath();
    ctx.roundRect(Math.min(tx, HEADER_W + vis - tw), y + 2, tw, SCROLL_H - 4, 3);
    ctx.fill();
  }

  // ── hit testing ──

  private hit(x: number, y: number): Hit {
    if (y >= this.cssH - SCROLL_H && x >= HEADER_W) return { kind: "hscroll" };
    if (y < RULER_H) {
      if (x >= HEADER_W) {
        const tl = store.view;
        for (let i = 0; i < this.commentMarkers.length; i++) if (Math.abs(this.xOf(this.commentMarkers[i]!.f) - x) <= 5 && y >= RULER_H - 15) return { kind: "comment", index: i };
        for (let i = tl.markers.length - 1; i >= 0; i--) if (Math.abs(this.xOf(tl.markers[i]!.f) - x) <= 6 && y >= 10 && y <= 26) return { kind: "marker", index: i };
      }
      return { kind: "ruler" };
    }
    const row = this.rowAt(y);
    if (x < HEADER_W) {
      if (!row) return { kind: "empty", track: null };
      if (x >= 6 && x <= 34) return { kind: "header", track: row.track, button: "target" };
      for (const b of this.headerButtons(row.track)) if (x >= b.x && x <= b.x + 22) return { kind: "header", track: row.track, button: b.id };
      return { kind: "header", track: row.track, button: null };
    }
    if (!row) return { kind: "empty", track: null };
    const f = this.fOf(x);
    const clips = row.track.clips;
    // edges first (prefer the clip whose edge is closest)
    for (let i = clips.length - 1; i >= 0; i--) {
      const c = clips[i]!;
      const xs = this.xOf(c.start);
      const xe = this.xOf(clipEnd(c));
      if (x >= xs - 1 && x <= xe + 1) {
        const wide = xe - xs > EDGE_PX * 3;
        if (wide && x - xs <= EDGE_PX) return { kind: "clip", clip: c, track: row.track, edge: "start" };
        if (wide && xe - x <= EDGE_PX) return { kind: "clip", clip: c, track: row.track, edge: "end" };
        if (f >= c.start && f < clipEnd(c)) return { kind: "clip", clip: c, track: row.track, edge: null };
      }
    }
    return { kind: "empty", track: row.track };
  }

  private pos(e: PointerEvent | MouseEvent | WheelEvent): [number, number] {
    const r = this.canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  // ── pointer handling ──

  private snapThreshold(): number {
    return SNAP_PX / this.ppf;
  }

  private onDown = (e: PointerEvent): void => {
    if (e.button !== 0) return;
    this.canvas.focus();
    const [x, y] = this.pos(e);
    const h = this.hit(x, y);
    const ro = store.readOnly;
    this.canvas.setPointerCapture(e.pointerId);
    switch (h.kind) {
      case "hscroll":
        this.drag = { mode: "hscroll", x0: x, scroll0: this.scrollX };
        return;
      case "ruler":
        this.drag = { mode: "scrub" };
        this.preview.pause();
        this.preview.scrubbing = true;
        store.setPlayhead(this.scrubFrame(x, e.shiftKey));
        return;
      case "comment": {
        const cm = this.commentMarkers[h.index]!;
        store.setPlayhead(cm.f);
        this.onCommentClick(cm.id);
        return;
      }
      case "marker": {
        const m = store.view.markers[h.index]!;
        store.setPlayhead(m.f);
        if (!ro) this.drag = { mode: "marker", index: h.index, x0: x, f0: m.f, moved: false };
        return;
      }
      case "header":
        this.headerClick(h.track, h.button, e);
        return;
      case "clip":
        this.clipDown(h, x, y, e);
        return;
      case "empty":
        if (!e.shiftKey && !e.ctrlKey && !e.metaKey) store.clearSelection();
        this.drag = { mode: "marquee", x0: x, y0: y, x1: x, y1: y, additive: e.shiftKey || e.ctrlKey || e.metaKey, base: new Set(store.selection) };
        return;
    }
  };

  private scrubFrame(x: number, noSnap: boolean): number {
    let f = Math.max(0, Math.round(this.fOf(x)));
    if (store.snapping && !noSnap) {
      const s = ops.snap(f, ops.snapCandidates(store.view, new Set(), []), this.snapThreshold());
      f = s.f;
    }
    return f;
  }

  private headerClick(track: Track, button: string | null, e: PointerEvent): void {
    if (button === "target") {
      if (e.shiftKey) {
        // shift-click: target only this track (of its kind)
        for (const t of store.doc.tracks) if (t.kind === track.kind) store.targets.delete(t.id);
        store.targets.add(track.id);
      } else if (store.targets.has(track.id)) store.targets.delete(track.id);
      else store.targets.add(track.id);
      store.emit("selection");
      this.invalidate();
      return;
    }
    if (!button) {
      // select all clips on the track
      store.select(track.clips.map((c) => c.id), e.shiftKey ? "add" : "set");
      return;
    }
    if (store.readOnly) return;
    const label = { lock: track.locked ? "Unlock track" : "Lock track", eye: track.hidden ? "Show track" : "Hide track", mute: track.muted ? "Unmute track" : "Mute track", solo: track.solo ? "Unsolo track" : "Solo track" }[button] ?? "Track";
    store.apply(label, (tl) =>
      ops.updateTrack(tl, track.id, (t) => {
        if (button === "lock") t.locked = !t.locked;
        if (button === "eye") t.hidden = !t.hidden;
        if (button === "mute") t.muted = !t.muted;
        if (button === "solo") t.solo = !t.solo;
      }),
    );
  }

  private clipDown(h: Extract<Hit, { kind: "clip" }>, x: number, y: number, e: PointerEvent): void {
    const { clip, track } = h;
    const ro = store.readOnly || track.locked;
    const alone = e.altKey || !store.linkedSelection;
    if (store.tool === "razor" && !ro) {
      let f = Math.round(this.fOf(x));
      if (store.snapping && !e.shiftKey) f = ops.snap(f, [store.playhead, ...store.view.markers.map((m) => m.f)], this.snapThreshold()).f;
      if (f > clip.start && f < clipEnd(clip)) razorAt(clip.id, f, e.altKey);
      return;
    }
    // edge drags: trim (Alt = roll with the adjacent clip)
    if (h.edge && !ro) {
      if (e.altKey) {
        const other = ops.adjacent(store.doc, clip.id, h.edge);
        if (other) {
          const left = h.edge === "end" ? clip : other;
          const right = h.edge === "end" ? other : clip;
          const pairs: Array<[string, string]> = [[left.id, right.id]];
          // linked partners with matching edits roll too
          if (store.linkedSelection) {
            for (const lid of ops.expandLinked(store.doc, [left.id])) {
              if (lid === left.id) continue;
              const r2 = ops.adjacent(store.doc, lid, "end");
              if (r2 && findClip(store.doc, lid)?.clip && clipEnd(findClip(store.doc, lid)!.clip) === clipEnd(left)) pairs.push([lid, r2.id]);
            }
          }
          this.drag = { mode: "roll", x0: x, pairs, anchorEnd: clipEnd(left), orig: store.doc, moved: false };
          store.select(pairs.flat());
          return;
        }
      }
      const edgeF = h.edge === "start" ? clip.start : clipEnd(clip);
      const group = alone ? [clip.id] : ops.expandLinked(store.doc, [clip.id]);
      const ids = group.filter((id) => {
        const c = findClip(store.doc, id)?.clip;
        return c && (h.edge === "start" ? c.start : clipEnd(c)) === edgeF;
      });
      store.select(ids);
      this.drag = { mode: "trim", x0: x, ids, edge: h.edge, anchor: clip, orig: store.doc, moved: false };
      return;
    }
    // selection
    const group = alone ? [clip.id] : ops.expandLinked(store.doc, [clip.id]);
    if (e.shiftKey || e.ctrlKey || e.metaKey) {
      store.select(group, store.selection.has(clip.id) ? "toggle" : "add");
    } else if (!store.selection.has(clip.id)) {
      store.select(group);
    }
    if (ro) return;
    const ids = [...store.selection].filter((id) => {
      const hit = findClip(store.doc, id);
      return hit && !hit.track.locked;
    });
    this.drag = { mode: "move", x0: x, y0: y, ids, anchor: clip, anchorTrack: track, orig: store.doc, moved: false };
  }

  private onMove = (e: PointerEvent): void => {
    const [x, y] = this.pos(e);
    const d = this.drag;
    if (!d) {
      this.updateCursor(x, y, e.altKey);
      return;
    }
    const thr = this.snapThreshold();
    const snapOn = store.snapping !== e.shiftKey; // Shift inverts snapping while dragging
    switch (d.mode) {
      case "hscroll": {
        const total = this.totalFrames() * this.ppf;
        const ratio = total / this.laneW;
        this.scrollX = Math.max(0, d.scroll0 + (x - d.x0) * ratio);
        this.invalidate();
        return;
      }
      case "scrub":
        store.setPlayhead(this.scrubFrame(x, e.shiftKey));
        this.autoScroll(x);
        return;
      case "marquee": {
        d.x1 = x;
        d.y1 = y;
        const f0 = this.fOf(Math.min(d.x0, d.x1));
        const f1 = this.fOf(Math.max(d.x0, d.x1));
        const y0 = Math.min(d.y0, d.y1);
        const y1 = Math.max(d.y0, d.y1);
        const ids = new Set(d.additive ? d.base : []);
        for (const r of this.rows) {
          if (r.y + r.h < y0 || r.y > y1) continue;
          for (const c of r.track.clips) if (clipEnd(c) > f0 && c.start < f1) ids.add(c.id);
        }
        store.select(store.linkedSelection ? ops.expandLinked(store.doc, ids) : ids);
        this.invalidate();
        return;
      }
      case "marker": {
        let f = Math.max(0, Math.round(d.f0 + (x - d.x0) / this.ppf));
        if (snapOn) f = ops.snap(f, ops.snapCandidates(store.doc, new Set(), [store.playhead]), thr).f;
        if (f !== d.f0 || d.moved) {
          d.moved = true;
          store.preview(ops.moveMarker(store.doc, d.index, f));
        }
        return;
      }
      case "move": {
        if (!d.moved && Math.abs(x - d.x0) < 3 && Math.abs(y - d.y0) < 3) return;
        d.moved = true;
        let delta = Math.round((x - d.x0) / this.ppf);
        const exclude = new Set(d.ids);
        this.snapLine = null;
        if (snapOn) {
          const edges: number[] = [];
          for (const id of d.ids) {
            const c = findClip(d.orig, id)?.clip;
            if (c) edges.push(c.start, clipEnd(c));
          }
          const s = ops.snapDelta(edges, delta, ops.snapCandidates(d.orig, exclude, [store.playhead]), thr);
          delta = s.delta;
          this.snapLine = s.target;
        }
        delta = ops.clampMoveDelta(d.orig, d.ids, delta);
        const trackMap = this.trackShift(d, y);
        store.preview(ops.moveClips(d.orig, d.ids, delta, trackMap));
        this.autoScroll(x);
        return;
      }
      case "trim": {
        let delta = Math.round((x - d.x0) / this.ppf);
        const edgeF = d.edge === "start" ? d.anchor.start : clipEnd(d.anchor);
        this.snapLine = null;
        if (snapOn) {
          const s = ops.snap(edgeF + delta, ops.snapCandidates(d.orig, new Set(d.ids), [store.playhead]), thr);
          delta = s.f - edgeF;
          this.snapLine = s.target;
        }
        d.moved = d.moved || delta !== 0;
        const ctx = sourceCtx();
        const out = ops.trim(d.orig, d.ids, d.edge, delta, ctx);
        store.preview(out);
        const c = findClip(out, d.anchor.id)?.clip;
        if (c) this.previewFrame(d.edge === "start" ? c.start : clipEnd(c) - 1);
        this.autoScroll(x);
        return;
      }
      case "roll": {
        let delta = Math.round((x - d.x0) / this.ppf);
        this.snapLine = null;
        if (snapOn) {
          const s = ops.snap(d.anchorEnd + delta, ops.snapCandidates(d.orig, new Set(d.pairs.flat()), [store.playhead]), thr);
          delta = s.f - d.anchorEnd;
          this.snapLine = s.target;
        }
        d.moved = d.moved || delta !== 0;
        const out = ops.roll(d.orig, d.pairs, delta, sourceCtx());
        store.preview(out);
        const c = findClip(out, d.pairs[0]![1])?.clip;
        if (c) this.previewFrame(c.start);
        return;
      }
    }
  };

  /** Map source tracks to destination tracks when dragging vertically (same kind only). */
  private trackShift(d: Extract<Drag, { mode: "move" }>, y: number): Record<string, string> {
    const row = this.rowAt(y);
    if (!row || row.track.kind !== d.anchorTrack.kind || row.track.id === d.anchorTrack.id) return {};
    const kindTracks = d.orig.tracks.filter((t) => t.kind === d.anchorTrack.kind);
    const from = kindTracks.findIndex((t) => t.id === d.anchorTrack.id);
    const to = kindTracks.findIndex((t) => t.id === row.track.id);
    const shift = to - from;
    const map: Record<string, string> = {};
    for (const id of d.ids) {
      const hit = findClip(d.orig, id);
      if (!hit || hit.track.kind !== d.anchorTrack.kind) continue;
      const i = kindTracks.findIndex((t) => t.id === hit.track.id);
      const dst = kindTracks[i + shift];
      if (!dst || dst.locked) return {};
      map[hit.track.id] = dst.id;
    }
    return map;
  }

  private previewFrame(f: number): void {
    this.preview.overrideFrame = Math.max(0, f);
    this.preview.requestDraw();
  }

  private autoScroll(x: number): void {
    if (x > this.cssW - 30) this.scrollX += 12;
    else if (x < HEADER_W + 20 && this.scrollX > 0) this.scrollX = Math.max(0, this.scrollX - 12);
  }

  private onUp = (e: PointerEvent): void => {
    const d = this.drag;
    this.drag = null;
    this.snapLine = null;
    try {
      this.canvas.releasePointerCapture(e.pointerId);
    } catch {
      /* not captured */
    }
    if (!d) return;
    if (this.preview.overrideFrame !== null) {
      this.preview.overrideFrame = null;
      this.preview.invalidate();
    }
    switch (d.mode) {
      case "scrub":
        this.preview.scrubbing = false;
        this.preview.invalidate();
        break;
      case "move":
        if (d.moved) store.commitPreview(Object.keys(this.trackShift(d, this.pos(e)[1])).length ? "Move clips to track" : "Move clips");
        else if (!e.shiftKey && !e.ctrlKey && !e.metaKey) {
          // plain click on an already-selected clip collapses the selection to it (and its links)
          const alone = e.altKey || !store.linkedSelection;
          store.select(alone ? [d.anchor.id] : ops.expandLinked(store.doc, [d.anchor.id]));
        }
        break;
      case "trim":
        if (d.moved) store.commitPreview("Trim");
        else store.preview(null);
        break;
      case "roll":
        if (d.moved) store.commitPreview("Roll edit");
        else store.preview(null);
        break;
      case "marker":
        if (d.moved) store.commitPreview("Move marker");
        break;
    }
    this.invalidate();
  };

  private onDbl = async (e: MouseEvent): Promise<void> => {
    const [x, y] = this.pos(e);
    const h = this.hit(x, y);
    if (h.kind === "marker" && !store.readOnly) {
      const m = store.doc.markers[h.index]!;
      const label = await promptDialog("Marker", "Label", m.label, "Save");
      if (label === null) return;
      store.apply("Rename marker", (tl) => {
        const out = structuredClone(tl);
        out.markers[h.index]!.label = label;
        return out;
      });
    } else if (h.kind === "header" && !h.button && !store.readOnly) {
      const name = await promptDialog("Track", "Track name", h.track.name, "Rename");
      if (name) store.apply("Rename track", (tl) => ops.updateTrack(tl, h.track.id, (t) => (t.name = name)));
    } else if (h.kind === "clip" && h.clip.type === "graphic") {
      store.selectLayer(h.clip.id, null);
      store.showPanel("graphics");
    }
  };

  private onWheel = (e: WheelEvent): void => {
    e.preventDefault();
    const [x] = this.pos(e);
    if (e.ctrlKey || e.metaKey) {
      const k = Math.exp(-e.deltaY * 0.0025);
      this.setZoom(this.ppf * k, Math.max(HEADER_W, x));
      return;
    }
    if (e.shiftKey || e.altKey) {
      this.scrollY = Math.max(0, Math.min(Math.max(0, this.contentH - (this.cssH - RULER_H - SCROLL_H)), this.scrollY + e.deltaY));
    } else {
      this.scrollX = Math.max(0, this.scrollX + (Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY));
    }
    this.invalidate();
  };

  private updateCursor(x: number, y: number, alt: boolean): void {
    const h = this.hit(x, y);
    let cur = "default";
    if (h.kind === "clip") {
      if (store.tool === "razor") cur = "crosshair";
      else if (h.edge) cur = alt || this.altDown ? "col-resize" : "ew-resize";
      else cur = "grab";
    } else if (h.kind === "ruler") cur = "text";
    else if (h.kind === "marker" || h.kind === "comment" || h.kind === "header") cur = "pointer";
    if (cur !== this.hoverCursor) {
      this.hoverCursor = cur;
      this.canvas.style.cursor = cur;
    }
  }
}

function defaultName(c: Clip): string {
  if (c.type === "text") return c.text.split("\n")[0]?.slice(0, 40) || "Title";
  if (c.type === "captions") return "Captions";
  if (c.type === "color") return "Color matte";
  if (c.type === "adjustment") return "Adjustment layer";
  if (c.type === "shape") return "Shape";
  if (c.type === "graphic") return "Graphic";
  const a = c.asset_id ? media.assets.get(c.asset_id) : undefined;
  return (a?.params?.filename as string | undefined) ?? a?.prompt ?? c.type;
}
