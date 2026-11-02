// Realtime program-monitor compositor (approximates render/compositor.py on a
// 2D canvas) + playback clock + audio mix. When playback stops (or scrubbing
// settles for 250 ms) the exact server frame replaces the approximation.

import { postBlob } from "./api";
import { media } from "./media";
import { clipEnd, prop, timelineDuration, type Clip, type Timeline, type Track } from "./model";
import { store } from "./store";
import { captionLines, css, renderText } from "./text";

type Ctx2D = CanvasRenderingContext2D;

interface VideoSlot {
  el: HTMLVideoElement;
  url: string;
  lastUsed: number;
  seeking: boolean;
}

interface AudioSlot {
  el: HTMLAudioElement;
  url: string;
  gain: GainNode | null;
  source: MediaElementAudioSourceNode | null;
  lastUsed: number;
}

const RAMP_S = 0.25;

export class Preview {
  readonly canvas: HTMLCanvasElement;
  private ctx: Ctx2D;
  private W = 640;
  private H = 360;
  private layerA = document.createElement("canvas");
  private layerB = document.createElement("canvas");
  private layerC = document.createElement("canvas");
  private videos = new Map<string, VideoSlot>();
  private audios = new Map<string, AudioSlot>();
  private audioCtx: AudioContext | null = null;
  playing = false;
  rate = 1;
  private clockT0 = 0;
  private clockF0 = 0;
  private raf = 0;
  private drawQueued = false;
  private exactTimer = 0;
  private exactAbort: AbortController | null = null;
  private exactToken = "";
  exactImg: HTMLImageElement;
  onExact: (state: "none" | "loading" | "shown" | "error", msg?: string) => void = () => {};
  onPlayState: () => void = () => {};
  /** Draw at this frame instead of the playhead (e.g. while trimming). */
  overrideFrame: number | null = null;
  scrubbing = false;
  private exactUrl: string | null = null;

  constructor(canvas: HTMLCanvasElement, exactImg: HTMLImageElement) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { alpha: false })!;
    this.exactImg = exactImg;
    media.on("media", () => this.requestDraw());
    media.on("fonts", () => this.requestDraw());
    setInterval(() => this.gc(), 4000);
  }

  // ── sizing ──

  resize(cssW: number, cssH: number): void {
    const tl = store.view;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const ar = tl.width / tl.height;
    let w = cssW;
    let h = w / ar;
    if (h > cssH) {
      h = cssH;
      w = h * ar;
    }
    w = Math.max(16, Math.floor(w));
    h = Math.max(9, Math.floor(h));
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
    this.exactImg.style.width = `${w}px`;
    this.exactImg.style.height = `${h}px`;
    let pw = Math.round(w * dpr);
    let ph = Math.round(h * dpr);
    if (ph > tl.height) {
      ph = tl.height;
      pw = Math.round(ph * ar);
    }
    pw -= pw % 2;
    ph -= ph % 2;
    if (pw !== this.W || ph !== this.H || this.canvas.width !== pw) {
      this.W = this.canvas.width = pw;
      this.H = this.canvas.height = ph;
      for (const l of [this.layerA, this.layerB, this.layerC]) {
        l.width = pw;
        l.height = ph;
      }
    }
    this.requestDraw();
  }

  // ── playback clock ──

  get frame(): number {
    return store.playhead;
  }

  play(rate = 1): void {
    if (store.duration <= 0) return;
    if (rate > 0 && store.playhead >= store.duration - 1) store.setPlayhead(0);
    this.ensureAudioCtx();
    this.rate = rate;
    this.clockT0 = performance.now();
    this.clockF0 = store.playhead;
    this.playing = true;
    this.hideExact();
    cancelAnimationFrame(this.raf);
    this.raf = requestAnimationFrame(this.tick);
    this.onPlayState();
  }

  pause(): void {
    if (!this.playing) return;
    this.playing = false;
    cancelAnimationFrame(this.raf);
    for (const v of this.videos.values()) v.el.pause();
    for (const a of this.audios.values()) a.el.pause();
    this.onPlayState();
    this.requestDraw();
    this.scheduleExact();
  }

  toggle(): void {
    if (this.playing) this.pause();
    else this.play(1);
  }

  private tick = (): void => {
    if (!this.playing) return;
    // rAF timestamps can predate play(); use the live clock so f never starts before the playhead
    const now = performance.now();
    const tl = store.view;
    const f = this.clockF0 + ((now - this.clockT0) / 1000) * tl.fps * this.rate;
    const end = Math.max(0, timelineDuration(tl) - 1);
    const outPoint = store.markOut !== null && store.playhead < store.markOut ? store.markOut : null;
    if (f >= (outPoint ?? end + 1) || (this.rate < 0 && f < 0)) {
      store.setPlayhead(Math.max(0, Math.min(f < 0 ? 0 : outPoint ?? end, end)));
      this.pause();
      return;
    }
    store.setPlayhead(Math.floor(f));
    this.render(Math.floor(f));
    this.syncAudio(f);
    this.raf = requestAnimationFrame(this.tick);
  };

  /** Seconds of clock drift a playing media element may accumulate before it is re-seeked. */
  private get tolerance(): number {
    return this.playing ? 0.2 : 0.5 / store.view.fps;
  }

  // ── drawing ──

  requestDraw(): void {
    if (this.playing || this.drawQueued) return;
    this.drawQueued = true;
    requestAnimationFrame(() => {
      this.drawQueued = false;
      if (!this.playing) this.render(this.overrideFrame ?? store.playhead);
    });
  }

  /** Something changed (doc or playhead): redraw now and fetch an exact frame once it settles. */
  invalidate(): void {
    if (this.playing) return;
    this.hideExact();
    this.requestDraw();
    this.scheduleExact();
  }

  render(f: number): void {
    const tl = store.view;
    const ctx = this.ctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1;
    ctx.filter = "none";
    ctx.globalCompositeOperation = "source-over";
    ctx.fillStyle = css(tl.background, { r: 0, g: 0, b: 0, a: 255 });
    ctx.fillRect(0, 0, this.W, this.H);
    const now = performance.now();
    for (const track of tl.tracks) {
      if (track.kind !== "video" || track.hidden) continue;
      this.drawTrack(ctx, tl, track, f, now);
    }
    if (this.playing) this.preroll(tl, f, now);
  }

  private drawTrack(ctx: Ctx2D, tl: Timeline, track: Track, f: number, now: number): void {
    const clips = track.clips.filter((c) => c.enabled).sort((a, b) => a.start - b.start);
    const active = clips.filter((c) => c.start <= f && f < clipEnd(c));
    if (!active.length) return;
    const clip = active[active.length - 1]!;
    const tr = clip.transition_in;
    if (tr && tr.duration > 0 && f < clip.start + tr.duration) {
      let prev: Clip | null = null;
      for (let i = clips.length - 1; i >= 0; i--) {
        const c = clips[i]!;
        if (c !== clip && clipEnd(c) <= clip.start + 1 && clipEnd(c) >= clip.start - 1) {
          prev = c;
          break;
        }
      }
      if (!prev && active.length > 1) prev = active[active.length - 2]!;
      const t = Math.max(0, Math.min(1, (f - clip.start + 0.5) / tr.duration));
      this.drawTransition(ctx, tl, prev, clip, f, tr.type, t, now);
      return;
    }
    for (const c of active) {
      if (c.type === "adjustment") this.adjustmentLayer(ctx, tl, c, f);
      else this.drawClip(ctx, tl, c, f, now);
    }
  }

  private layer(l: HTMLCanvasElement): Ctx2D {
    const c = l.getContext("2d")!;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.globalAlpha = 1;
    c.filter = "none";
    c.globalCompositeOperation = "source-over";
    c.clearRect(0, 0, this.W, this.H);
    return c;
  }

  private drawTransition(ctx: Ctx2D, tl: Timeline, a: Clip | null, b: Clip, f: number, kind: string, t: number, now: number): void {
    const A = this.layer(this.layerA);
    const B = this.layer(this.layerB);
    if (a) this.drawClip(A, tl, a, Math.min(f, clipEnd(a) - 1), now);
    this.drawClip(B, tl, b, f, now);
    const W = this.W;
    const H = this.H;
    ctx.save();
    if (kind.startsWith("slide")) {
      const dx = kind === "slide-left" ? -1 : kind === "slide-right" ? 1 : 0;
      const dy = kind === "slide-up" ? -1 : 0;
      const e = t * t * (3 - 2 * t);
      ctx.drawImage(this.layerA, Math.round(dx * e * W), Math.round(dy * e * H));
      ctx.drawImage(this.layerB, Math.round(-dx * (1 - e) * W), Math.round(-dy * (1 - e) * H));
    } else if (kind === "dip-black" || kind === "dip-white") {
      const [src, k] = t < 0.5 ? [this.layerA, 1 - 2 * t] : [this.layerB, 2 * t - 1];
      ctx.fillStyle = kind === "dip-black" ? "#000" : "#fff";
      ctx.fillRect(0, 0, W, H);
      ctx.globalAlpha = Math.max(0, k);
      ctx.drawImage(src, 0, 0);
    } else if (kind === "wipe-left" || kind === "wipe-right" || kind === "wipe") {
      const cut = Math.round(t * W);
      const bx = kind === "wipe-right" ? W - cut : 0;
      const ax = kind === "wipe-right" ? 0 : cut;
      ctx.drawImage(this.layerA, ax, 0, W - cut, H, ax, 0, W - cut, H);
      if (cut > 0) ctx.drawImage(this.layerB, bx, 0, cut, H, bx, 0, cut, H);
    } else if (kind === "zoom") {
      ctx.globalAlpha = 1 - t;
      ctx.drawImage(this.layerA, 0, 0);
      const s = 1.25 - 0.25 * t;
      ctx.globalAlpha = t;
      ctx.translate(W / 2, H / 2);
      ctx.scale(s, s);
      ctx.drawImage(this.layerB, -W / 2, -H / 2);
    } else {
      // crossfade: premultiplied mix of A and B, then over the canvas
      const C = this.layer(this.layerC);
      C.globalCompositeOperation = "lighter";
      C.globalAlpha = 1 - t;
      C.drawImage(this.layerA, 0, 0);
      C.globalAlpha = t;
      C.drawImage(this.layerB, 0, 0);
      ctx.drawImage(this.layerC, 0, 0);
    }
    ctx.restore();
  }

  private boxPx(clip: Clip): [number, number, number, number] {
    const [x = 0, y = 0, w = 1, hh = 1] = clip.box;
    return [Math.round(x * this.W), Math.round(y * this.H), Math.max(2, Math.round(w * this.W)), Math.max(2, Math.round(hh * this.H))];
  }

  private drawClip(ctx: Ctx2D, tl: Timeline, clip: Clip, f: number, now: number): void {
    const fRel = f - clip.start;
    const [bx, by, bw, bh] = this.boxPx(clip);
    let opacity = prop(clip, "opacity", fRel, clip.transform.opacity);
    let scale = prop(clip, "scale", fRel, clip.transform.scale);
    const rotation = prop(clip, "rotation", fRel, clip.transform.rotation);
    let ox = prop(clip, "x", fRel, clip.transform.x);
    let oy = prop(clip, "y", fRel, clip.transform.y);
    let visibleChars: number | null = null;
    let wipe = 1;
    for (const [anim, phase] of [
      [clip.anim_in, "in"],
      [clip.anim_out, "out"],
    ] as const) {
      if (!anim || anim.duration <= 0) continue;
      let p: number;
      if (phase === "in") {
        if (fRel >= anim.duration) continue;
        p = (fRel + 1) / anim.duration;
      } else {
        const start = clip.duration - anim.duration;
        if (fRel < start) continue;
        p = 1 - (fRel - start + 1) / anim.duration;
      }
      p = Math.max(0, Math.min(1, p));
      const e = 1 - (1 - p) ** 3;
      const k = anim.type;
      if (k === "fade") opacity *= p;
      else if (k.startsWith("slide-")) {
        const d = 0.06 * (1 - e);
        if (k === "slide-up") oy += d;
        else if (k === "slide-down") oy -= d;
        else if (k === "slide-left") ox += d;
        else ox -= d;
        opacity *= Math.min(1, p * 1.6);
      } else if (k === "pop") {
        const os = 1 + 0.12 * Math.sin(Math.PI * e);
        scale *= p < 1 ? (0.6 + 0.4 * e) * os : 1;
        opacity *= Math.min(1, p * 2);
      } else if (k === "zoom-in") {
        scale *= 0.85 + 0.15 * e;
        opacity *= p;
      } else if (k === "zoom-out") {
        scale *= 1.15 - 0.15 * e;
        opacity *= p;
      } else if (k === "wipe") wipe = Math.min(wipe, e);
      else if (k === "typewriter" && clip.type === "text") {
        if (phase === "in") visibleChars = Math.ceil(clip.text.length * p);
        else opacity *= p;
      }
    }
    if (opacity <= 0.001 || scale <= 0.001) return;
    const cx = bx + bw / 2 + ox * this.W;
    const cy = by + bh / 2 + oy * this.H;
    ctx.save();
    ctx.globalAlpha *= Math.max(0, Math.min(1, opacity));
    ctx.translate(cx, cy);
    if (rotation) ctx.rotate((rotation * Math.PI) / 180);
    if (scale !== 1) ctx.scale(scale, scale);
    if (wipe < 1) {
      ctx.beginPath();
      ctx.rect(-bw / 2, -bh / 2, Math.round(bw * wipe), bh);
      ctx.clip();
    }
    if (clip.adjust) ctx.filter = adjustFilter(clip, fRel);
    this.content(ctx, tl, clip, f, fRel, bw, bh, visibleChars, now);
    ctx.restore();
  }

  private adjustmentLayer(ctx: Ctx2D, tl: Timeline, clip: Clip, f: number): void {
    if (!clip.adjust) return;
    void tl;
    const [bx, by, bw, bh] = this.boxPx(clip);
    const fRel = f - clip.start;
    const k = Math.max(0, Math.min(1, prop(clip, "opacity", fRel, clip.transform.opacity)));
    const C = this.layer(this.layerC);
    C.drawImage(this.canvas, 0, 0);
    ctx.save();
    ctx.beginPath();
    ctx.rect(bx, by, bw, bh);
    ctx.clip();
    ctx.globalAlpha = k;
    ctx.filter = adjustFilter(clip, fRel);
    ctx.drawImage(this.layerC, 0, 0);
    ctx.restore();
  }

  private content(ctx: Ctx2D, tl: Timeline, clip: Clip, f: number, fRel: number, bw: number, bh: number, visibleChars: number | null, now: number): void {
    const x0 = -bw / 2;
    const y0 = -bh / 2;
    const textScale = this.H / 1080;
    switch (clip.type) {
      case "color":
        ctx.fillStyle = css(clip.color, { r: 0, g: 0, b: 0, a: 255 });
        ctx.fillRect(x0, y0, bw, bh);
        return;
      case "shape":
        drawShape(ctx, clip, x0, y0, bw, bh, textScale);
        return;
      case "text": {
        const img = renderText(clip.text, clip.style, bw, bh, textScale, visibleChars);
        if (img) ctx.drawImage(img, x0, y0);
        return;
      }
      case "captions": {
        const img = this.captions(clip, f, bw, bh, textScale, tl.fps);
        if (img) ctx.drawImage(img, x0, y0);
        return;
      }
      case "image": {
        if (!clip.asset_id) return;
        const img = media.image(clip.asset_id);
        if (img.complete && img.naturalWidth) drawFit(ctx, img, img.naturalWidth, img.naturalHeight, clip, x0, y0, bw, bh, fRel);
        return;
      }
      case "video": {
        if (clip.nest) {
          ctx.fillStyle = "#1d2230";
          ctx.fillRect(x0, y0, bw, bh);
          ctx.fillStyle = "#8A90A2";
          ctx.font = `${Math.max(10, bh / 14)}px Inter, sans-serif`;
          ctx.textAlign = "center";
          ctx.fillText("Nested sequence", 0, 0);
          ctx.textAlign = "start";
          return;
        }
        if (!clip.asset_id) return;
        const st = media.status.get(clip.asset_id);
        if (st?.probe?.still) {
          const img = media.image(clip.asset_id);
          if (img.complete && img.naturalWidth) drawFit(ctx, img, img.naturalWidth, img.naturalHeight, clip, x0, y0, bw, bh, fRel);
          return;
        }
        const v = this.videoAt(clip, fRel, tl.fps, now);
        if (v && v.readyState >= 2 && v.videoWidth) drawFit(ctx, v, v.videoWidth, v.videoHeight, clip, x0, y0, bw, bh, fRel);
        return;
      }
      default:
        return;
    }
  }

  private captions(clip: Clip, f: number, bw: number, bh: number, scale: number, fps: number): HTMLCanvasElement | null {
    const cap = clip.captions;
    if (!cap || !cap.words.length) return null;
    const words = cap.words;
    const lines = captionLines(words, cap.max_chars);
    let current: number | null = null;
    for (let li = 0; li < lines.length; li++) {
      const idxs = lines[li]!;
      const s = words[idxs[0]!]!.s;
      const e = words[idxs[idxs.length - 1]!]!.e;
      const nxt = li + 1 < lines.length ? words[lines[li + 1]![0]!]!.s : e + Math.floor(fps / 2);
      if (s <= f && f < Math.max(e, Math.min(nxt, e + fps))) {
        current = li;
        break;
      }
    }
    if (current === null) return null;
    const idxs = lines[current]!;
    let hl: number | null = null;
    idxs.forEach((wi, j) => {
      if (words[wi]!.s <= f) hl = j;
    });
    let text: string;
    let hlIdx: number | null;
    if (cap.mode === "word") {
      text = words[idxs[hl ?? 0]!]!.w;
      hlIdx = null;
    } else {
      text = idxs.map((i) => words[i]!.w).join(" ");
      hlIdx = cap.mode === "karaoke" ? hl : null;
    }
    return renderText(text, clip.style, bw, bh, scale, null, hlIdx);
  }

  // ── video elements ──

  private slotFor(clip: Clip, now: number): VideoSlot | null {
    if (!clip.asset_id) return null;
    void media.ensure(clip.asset_id).catch(() => undefined);
    const url = media.playUrl(clip.asset_id);
    let slot = this.videos.get(clip.id);
    if (slot && slot.url !== url && !this.playing) {
      this.dropVideo(clip.id);
      slot = undefined;
    }
    if (!slot) {
      const el = document.createElement("video");
      el.muted = true;
      el.playsInline = true;
      el.preload = "auto";
      el.crossOrigin = "anonymous";
      el.src = url;
      const s: VideoSlot = { el, url, lastUsed: now, seeking: false };
      el.addEventListener("seeked", () => {
        s.seeking = false;
        this.requestDraw();
      });
      el.addEventListener("loadeddata", () => this.requestDraw());
      el.addEventListener("error", () => undefined);
      this.videos.set(clip.id, s);
      slot = s;
    }
    slot.lastUsed = now;
    return slot;
  }

  private videoAt(clip: Clip, fRel: number, fps: number, now: number): HTMLVideoElement | null {
    const slot = this.slotFor(clip, now);
    if (!slot) return null;
    const el = slot.el;
    const target = clip.freeze ? clip.in / fps : (clip.in + fRel * clip.speed) / fps;
    const forwardPlay = this.playing && this.rate > 0 && !clip.freeze && !clip.reverse;
    if (forwardPlay) {
      const rate = Math.max(0.0625, Math.min(16, clip.speed * this.rate));
      if (Math.abs(el.playbackRate - rate) > 1e-3) el.playbackRate = rate;
      if (el.paused && el.readyState >= 2) void el.play().catch(() => undefined);
      if (Math.abs(el.currentTime - target) > this.tolerance && !slot.seeking) this.seek(slot, target);
    } else {
      if (!el.paused) el.pause();
      if (Math.abs(el.currentTime - target) > this.tolerance && !slot.seeking && el.readyState >= 1) this.seek(slot, target);
    }
    return el;
  }

  private seek(slot: VideoSlot, t: number): void {
    slot.seeking = true;
    const d = slot.el.duration;
    slot.el.currentTime = Number.isFinite(d) && d > 0 ? Math.min(t, d - 0.001) : t;
    setTimeout(() => (slot.seeking = false), 800);
  }

  /** During playback, warm up video elements for clips starting within ~1.5 s. */
  private preroll(tl: Timeline, f: number, now: number): void {
    const horizon = f + tl.fps * 1.5;
    for (const t of tl.tracks) {
      if (t.kind !== "video" || t.hidden) continue;
      for (const c of t.clips) {
        if (c.type !== "video" || !c.enabled || c.nest || c.start <= f || c.start > horizon) continue;
        const slot = this.slotFor(c, now);
        if (!slot) continue;
        const target = c.in / tl.fps;
        if (Math.abs(slot.el.currentTime - target) > 0.05 && !slot.seeking && slot.el.readyState >= 1) this.seek(slot, target);
      }
    }
  }

  private dropVideo(id: string): void {
    const s = this.videos.get(id);
    if (!s) return;
    s.el.pause();
    s.el.removeAttribute("src");
    s.el.load();
    this.videos.delete(id);
  }

  private gc(): void {
    const now = performance.now();
    for (const [id, s] of this.videos) if (now - s.lastUsed > 6000) this.dropVideo(id);
    for (const [id, s] of this.audios) {
      if (now - s.lastUsed > 6000) {
        s.el.pause();
        s.source?.disconnect();
        s.gain?.disconnect();
        s.el.removeAttribute("src");
        s.el.load();
        this.audios.delete(id);
      }
    }
  }

  // ── audio ──

  private ensureAudioCtx(): void {
    if (!this.audioCtx) {
      try {
        this.audioCtx = new AudioContext();
      } catch {
        this.audioCtx = null;
      }
    }
    if (this.audioCtx?.state === "suspended") void this.audioCtx.resume();
  }

  private syncAudio(fFloat: number): void {
    const tl = store.view;
    const fps = tl.fps;
    const now = performance.now();
    const audible = new Set<string>();
    if (this.rate > 0 && this.rate <= 4) {
      const tracks = tl.tracks.filter((t) => t.kind === "audio");
      const soloed = tracks.some((t) => t.solo);
      const speech = speechRanges(tl);
      for (const t of tracks) {
        if (t.muted || (soloed && !t.solo)) continue;
        for (const c of t.clips) {
          if (!c.enabled || c.type !== "audio" || !c.asset_id) continue;
          if (fFloat < c.start - fps * 0.5 || fFloat >= clipEnd(c)) continue;
          const slot = this.audioSlot(c, now);
          if (!slot) continue;
          audible.add(c.id);
          const fRel = fFloat - c.start;
          const el = slot.el;
          if (fRel < 0) {
            // about to start: park at the in-point
            if (!el.paused) el.pause();
            const target = c.in / fps;
            if (Math.abs(el.currentTime - target) > 0.05 && el.readyState >= 1) el.currentTime = target;
            continue;
          }
          const target = (c.in + fRel * c.speed) / fps;
          const rate = Math.max(0.0625, Math.min(16, c.speed * this.rate));
          if (Math.abs(el.playbackRate - rate) > 1e-3) el.playbackRate = rate;
          if (Math.abs(el.currentTime - target) > 0.15 && el.readyState >= 1) el.currentTime = target;
          if (el.paused) void el.play().catch(() => undefined);
          const g = clipGain(c, t, fRel, fps, fFloat / fps, speech);
          if (slot.gain && this.audioCtx) slot.gain.gain.setTargetAtTime(g, this.audioCtx.currentTime, 0.015);
          else el.volume = Math.max(0, Math.min(1, g));
        }
      }
    }
    for (const [id, s] of this.audios) if (!audible.has(id) && !s.el.paused) s.el.pause();
  }

  private audioSlot(clip: Clip, now: number): AudioSlot | null {
    if (!clip.asset_id) return null;
    let s = this.audios.get(clip.id);
    if (!s) {
      void media.ensure(clip.asset_id).catch(() => undefined);
      const asset = media.assets.get(clip.asset_id);
      // video assets: the proxy carries a stereo AAC track; audio assets play the original
      const url = asset?.kind === "video" && media.status.get(clip.asset_id)?.proxy ? media.playUrl(clip.asset_id) : media.fileUrl(clip.asset_id);
      const el = new Audio();
      el.preload = "auto";
      el.crossOrigin = "anonymous";
      el.src = url;
      (el as HTMLMediaElement & { preservesPitch?: boolean }).preservesPitch = true;
      let gain: GainNode | null = null;
      let source: MediaElementAudioSourceNode | null = null;
      if (this.audioCtx) {
        try {
          source = this.audioCtx.createMediaElementSource(el);
          gain = this.audioCtx.createGain();
          source.connect(gain).connect(this.audioCtx.destination);
        } catch {
          gain = null;
        }
      }
      s = { el, url, gain, source, lastUsed: now };
      this.audios.set(clip.id, s);
    }
    s.lastUsed = now;
    return s;
  }

  // ── exact server frame ──

  private hideExact(): void {
    clearTimeout(this.exactTimer);
    this.exactAbort?.abort();
    this.exactAbort = null;
    if (this.exactImg.style.display !== "none") {
      this.exactImg.style.display = "none";
      this.onExact("none");
    }
  }

  scheduleExact(): void {
    clearTimeout(this.exactTimer);
    if (this.playing) return;
    this.exactTimer = window.setTimeout(() => void this.fetchExact(), 250);
  }

  private async fetchExact(): Promise<void> {
    if (this.playing || this.scrubbing || store.transient) return;
    const tl = store.doc;
    if (timelineDuration(tl) <= 0) return;
    const f = this.overrideFrame ?? store.playhead;
    const height = Math.min(tl.height, Math.max(180, this.H));
    const token = `${store.revision}:${f}:${height}`;
    if (token === this.exactToken && this.exactImg.style.display !== "none") return;
    this.exactAbort?.abort();
    const ac = new AbortController();
    this.exactAbort = ac;
    this.onExact("loading");
    try {
      const blob = await postBlob("/api/render/frame", { timeline: tl, f, height }, ac.signal);
      if (ac.signal.aborted || this.playing) return;
      if (this.exactUrl) URL.revokeObjectURL(this.exactUrl);
      this.exactUrl = URL.createObjectURL(blob);
      this.exactImg.onload = () => {
        if (ac.signal.aborted || this.playing) return;
        this.exactImg.style.display = "block";
        this.exactToken = token;
        this.onExact("shown");
      };
      this.exactImg.src = this.exactUrl;
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      this.onExact("error", (e as Error).message);
    }
  }

  /** Render the current frame (realtime approximation) into a data URL for thumbnails/drawings. */
  snapshot(): string {
    return this.canvas.toDataURL("image/jpeg", 0.8);
  }
}

// ── helpers ──────────────────────────────────────────────────────────

function adjustFilter(clip: Clip, fRel: number): string {
  const a = clip.adjust!;
  const exposure = prop(clip, "exposure", fRel, a.exposure);
  const contrast = prop(clip, "contrast", fRel, a.contrast);
  const saturation = prop(clip, "saturation", fRel, a.saturation);
  const brightness = prop(clip, "brightness", fRel, a.brightness);
  const parts: string[] = [];
  const mult = 2 ** exposure * (1 + brightness * 0.6);
  if (Math.abs(mult - 1) > 1e-3) parts.push(`brightness(${mult.toFixed(4)})`);
  if (contrast) parts.push(`contrast(${(1 + contrast).toFixed(4)})`);
  if (saturation !== 1) parts.push(`saturate(${saturation.toFixed(4)})`);
  if (a.temperature) parts.push(a.temperature > 0 ? `sepia(${(a.temperature * 0.25).toFixed(3)})` : `hue-rotate(${(a.temperature * -12).toFixed(2)}deg)`);
  if (a.tint) parts.push(`hue-rotate(${(a.tint * 8).toFixed(2)}deg)`);
  return parts.length ? parts.join(" ") : "none";
}

function drawFit(ctx: Ctx2D, src: CanvasImageSource, iw: number, ih: number, clip: Clip, x: number, y: number, bw: number, bh: number, fRel: number): void {
  const fit = clip.fit;
  if (fit === "fill") {
    ctx.drawImage(src, 0, 0, iw, ih, x, y, bw, bh);
    return;
  }
  if (fit === "reframe") {
    const s = bh / ih;
    const nw = Math.max(bw, Math.round(iw * s));
    const cxN = clip.keyframes.crop_x ? prop(clip, "crop_x", fRel, 0.5) : 0.5;
    if (nw <= bw) {
      ctx.drawImage(src, 0, 0, iw, ih, x, y, nw, bh);
      return;
    }
    const x0 = Math.round(Math.min(Math.max(0, cxN * nw - bw / 2), nw - bw));
    const sx = (x0 / nw) * iw;
    const sw = (bw / nw) * iw;
    ctx.drawImage(src, sx, 0, sw, ih, x, y, bw, bh);
    return;
  }
  const s = fit === "contain" ? Math.min(bw / iw, bh / ih) : Math.max(bw / iw, bh / ih);
  const nw = Math.max(1, Math.round(iw * s));
  const nh = Math.max(1, Math.round(ih * s));
  if (fit === "contain") {
    ctx.drawImage(src, 0, 0, iw, ih, x + Math.floor((bw - nw) / 2), y + Math.floor((bh - nh) / 2), nw, nh);
    return;
  }
  // cover: crop the source to the box
  const sw = bw / s;
  const sh = bh / s;
  ctx.drawImage(src, (iw - sw) / 2, (ih - sh) / 2, sw, sh, x, y, bw, bh);
}

function drawShape(ctx: Ctx2D, clip: Clip, x: number, y: number, bw: number, bh: number, scale: number): void {
  const sh = clip.shape;
  const r = sh.radius * Math.min(bw, bh);
  const path = () => {
    ctx.beginPath();
    if (sh.kind === "ellipse") ctx.ellipse(x + bw / 2, y + bh / 2, bw / 2, bh / 2, 0, 0, Math.PI * 2);
    else ctx.roundRect(x, y, bw, bh, Math.min(r, bw / 2, bh / 2));
  };
  if (sh.gradient && sh.gradient.length >= 2) {
    const g = ctx.createLinearGradient(0, y, 0, y + bh);
    g.addColorStop(0, css(sh.gradient[0]));
    g.addColorStop(1, css(sh.gradient[1]));
    ctx.fillStyle = g;
    path();
    ctx.fill();
    return;
  }
  const sw = Math.round(sh.stroke_width * scale);
  if (sh.kind === "line") {
    ctx.strokeStyle = css(sh.stroke ?? sh.fill);
    ctx.lineWidth = Math.max(1, sw || bh);
    ctx.beginPath();
    ctx.moveTo(x, y + bh / 2);
    ctx.lineTo(x + bw, y + bh / 2);
    ctx.stroke();
    return;
  }
  path();
  if (sh.fill) {
    ctx.fillStyle = css(sh.fill);
    ctx.fill();
  }
  if (sh.stroke && sw > 0) {
    ctx.save();
    ctx.clip(); // PIL draws the outline inside the shape
    ctx.strokeStyle = css(sh.stroke);
    ctx.lineWidth = sw * 2;
    path();
    ctx.stroke();
    ctx.restore();
  }
}

function speechRanges(tl: Timeline): Array<[number, number]> {
  const fps = tl.fps;
  const rng: Array<[number, number]> = [];
  const tracks = tl.tracks.filter((t) => t.kind === "audio");
  const soloed = tracks.some((t) => t.solo);
  for (const t of tracks) {
    if (t.muted || (soloed && !t.solo)) continue;
    for (const c of t.clips) if (c.enabled && c.type === "audio" && (c.role === "dialogue" || c.role === "vo")) rng.push([c.start / fps, clipEnd(c) / fps]);
  }
  rng.sort((a, b) => a[0] - b[0]);
  const merged: Array<[number, number]> = [];
  for (const [s, e] of rng) {
    const last = merged[merged.length - 1];
    if (last && s <= last[1] + 2 * RAMP_S) last[1] = Math.max(last[1], e);
    else merged.push([s, e]);
  }
  return merged;
}

/** Linear gain at a clip-relative frame (volume, keyframes, fades, ducking) — mirrors render/audio.py. */
export function clipGain(c: Clip, track: Track, fRel: number, fps: number, tSec: number, speech: Array<[number, number]>): number {
  let g = c.volume * track.volume;
  const kfs = c.keyframes.volume;
  if (kfs?.length) {
    const pts = [...kfs].sort((a, b) => a.f - b.f);
    const t = fRel;
    let v = pts[pts.length - 1]!.v;
    if (t <= pts[0]!.f) v = pts[0]!.v;
    else
      for (let i = 0; i < pts.length - 1; i++) {
        const a = pts[i]!;
        const b = pts[i + 1]!;
        if (t >= a.f && t < b.f) {
          v = a.v + ((b.v - a.v) * (t - a.f)) / Math.max(1e-3, b.f - a.f);
          break;
        }
      }
    g *= v;
  }
  if (c.fade_in > 0 && fRel < c.fade_in) g *= Math.max(0, fRel / c.fade_in);
  if (c.fade_out > 0 && fRel > c.duration - c.fade_out) g *= Math.max(0, (c.duration - fRel) / c.fade_out);
  if (c.duck_db && speech.length && c.role !== "dialogue" && c.role !== "vo") {
    const low = 10 ** (-Math.abs(c.duck_db) / 20);
    let member = 0;
    for (const [s, e] of speech) member += Math.max(0, Math.min(1, Math.min((tSec - (s - RAMP_S)) / RAMP_S, (e + RAMP_S - tSec) / RAMP_S)));
    g *= 1 - Math.min(1, member) * (1 - low);
  }
  return Math.max(0, g);
}
