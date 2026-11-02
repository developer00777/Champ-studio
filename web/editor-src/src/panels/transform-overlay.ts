// Direct manipulation in the Program monitor (Premiere's "select in program"):
// click picks the topmost clip — or, inside a selected graphic, the topmost
// layer — under the pointer; drag moves it; corner/edge handles resize its box
// (Shift keeps the aspect ratio). Arrow keys nudge while the overlay has focus.

import * as gfx from "../graphics";
import { clipEnd, prop, type Clip } from "../model";
import * as ops from "../ops";
import { store } from "../store";
import { h } from "../ui";

type Rect = { x: number; y: number; w: number; h: number };
type Target = { clip: Clip; layer: Clip | null; rect: Rect; rotation: number };
type Handle = "move" | "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";

const VISUAL = new Set(["video", "image", "text", "shape", "color", "graphic", "captions"]);

export class TransformOverlay {
  readonly el: HTMLElement;
  private frame: HTMLElement;
  private label: HTMLElement;
  private canvas: HTMLCanvasElement;
  private raf = 0;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    this.label = h("span", { class: "xf-label" });
    this.frame = h("div", { class: "xf-frame" }, this.label);
    for (const k of ["nw", "n", "ne", "e", "se", "s", "sw", "w"] as const) this.frame.append(h("div", { class: `xf-handle h-${k}`, "data-h": k }));
    this.el = h("div", { class: "xf-overlay", tabindex: 0, "aria-label": "Program monitor selection: click to select, drag to move, handles to resize" }, this.frame);
    this.el.addEventListener("pointerdown", (e) => this.down(e));
    this.el.addEventListener("dblclick", (e) => this.dbl(e));
    this.el.addEventListener("keydown", (e) => this.key(e));
    const later = () => {
      cancelAnimationFrame(this.raf);
      this.raf = requestAnimationFrame(() => this.sync());
    };
    for (const ev of ["selection", "doc", "playhead", "readonly"] as const) store.on(ev, later);
  }

  /** Match the canvas's on-screen box and redraw the selection frame. */
  sync(): void {
    const c = this.canvas;
    this.el.style.left = `${c.offsetLeft}px`;
    this.el.style.top = `${c.offsetTop}px`;
    this.el.style.width = c.style.width;
    this.el.style.height = c.style.height;
    const t = this.target();
    if (!t) {
      this.frame.style.display = "none";
      return;
    }
    const W = c.clientWidth;
    const H = c.clientHeight;
    this.frame.style.display = "";
    this.frame.style.left = `${t.rect.x * W}px`;
    this.frame.style.top = `${t.rect.y * H}px`;
    this.frame.style.width = `${Math.max(2, t.rect.w * W)}px`;
    this.frame.style.height = `${Math.max(2, t.rect.h * H)}px`;
    this.frame.style.transform = t.rotation ? `rotate(${t.rotation}deg)` : "";
    const fixed = !!t.layer?.pin || store.readOnly || !!(t.layer ?? t.clip).locked;
    this.frame.classList.toggle("fixed", fixed);
    this.frame.classList.toggle("layer", !!t.layer);
    this.label.textContent = t.layer ? `${t.layer.name || t.layer.type}${t.layer.pin ? " · pinned" : ""}` : t.clip.name || t.clip.type;
  }

  // ── geometry ──

  /** Canvas-normalized rect of a clip (box + animated offset + scale) at the playhead. */
  private clipRect(c: Clip, f: number, parent: Rect = { x: 0, y: 0, w: 1, h: 1 }): Rect {
    const fRel = f - c.start;
    const [bx = 0, by = 0, bw = 1, bh = 1] = c.box;
    const s = prop(c, "scale", fRel, c.transform.scale);
    const ox = prop(c, "x", fRel, c.transform.x);
    const oy = prop(c, "y", fRel, c.transform.y);
    const cx = bx + bw / 2 + ox;
    const cy = by + bh / 2 + oy;
    const w = bw * s;
    const hh = bh * s;
    return { x: parent.x + (cx - w / 2) * parent.w, y: parent.y + (cy - hh / 2) * parent.h, w: w * parent.w, h: hh * parent.h };
  }

  private target(): Target | null {
    const hit = store.primary();
    if (!hit || store.selection.size !== 1 || !VISUAL.has(hit.clip.type)) return null;
    const c = hit.clip;
    const f = store.playhead;
    if (f < c.start || f >= clipEnd(c)) return null;
    const rect = this.clipRect(c, f);
    if (store.layer?.clip === c.id && c.graphic) {
      const raw = c.graphic.layers.find((l) => l.id === store.layer!.id);
      if (!raw) return null;
      const layer = gfx.applyBindings(raw, gfx.controlValues(c));
      const fr = f - c.start;
      if (fr < layer.start || (!layer.hold && fr >= layer.start + layer.duration)) return null;
      const lr = this.clipRect(layer, fr, rect);
      return { clip: c, layer: raw, rect: lr, rotation: prop(layer, "rotation", fr - layer.start, layer.transform.rotation) };
    }
    return { clip: c, layer: null, rect, rotation: prop(c, "rotation", f - c.start, c.transform.rotation) };
  }

  /** Topmost clip (or graphic layer) under a canvas-normalized point. */
  private pick(px: number, py: number): { clip: Clip; layer: string | null } | null {
    const f = store.playhead;
    const inside = (r: Rect) => px >= r.x && px <= r.x + r.w && py >= r.y && py <= r.y + r.h;
    const sel = store.primary()?.clip;
    if (sel?.graphic && f >= sel.start && f < clipEnd(sel)) {
      const gr = this.clipRect(sel, f);
      const fr = f - sel.start;
      const layers = gfx.resolveLayers(sel);
      for (let i = layers.length - 1; i >= 0; i--) {
        const l = layers[i]!;
        const raw = sel.graphic.layers.find((x) => x.id === l.id);
        if (!l.enabled || raw?.locked || fr < l.start || fr >= l.start + l.duration) continue;
        if (inside(this.clipRect(l, fr, gr))) return { clip: sel, layer: l.id };
      }
      if (inside(gr) && store.layer) return { clip: sel, layer: null };
    }
    const tracks = store.view.tracks.filter((t) => t.kind === "video" && !t.hidden && !t.locked);
    for (let ti = tracks.length - 1; ti >= 0; ti--) {
      for (const c of [...tracks[ti]!.clips].reverse()) {
        if (!c.enabled || !VISUAL.has(c.type) || f < c.start || f >= clipEnd(c)) continue;
        const r = this.clipRect(c, f);
        if (c.box[2]! >= 0.999 && c.box[3]! >= 0.999 && c.type !== "graphic" && c.type !== "text") continue; // full-frame picture: pick only when nothing else is there
        if (inside(r)) return { clip: c, layer: null };
      }
    }
    for (let ti = tracks.length - 1; ti >= 0; ti--) {
      const c = tracks[ti]!.clips.find((x) => x.enabled && VISUAL.has(x.type) && f >= x.start && f < clipEnd(x));
      if (c) return { clip: c, layer: null };
    }
    return null;
  }

  // ── interaction ──

  private norm(e: PointerEvent | MouseEvent): [number, number] {
    const r = this.el.getBoundingClientRect();
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
  }

  private down(e: PointerEvent): void {
    if (e.button !== 0) return;
    this.el.focus({ preventScroll: true });
    const handle = (e.target as HTMLElement).dataset.h as Handle | undefined;
    let t = this.target();
    const [px, py] = this.norm(e);
    const onFrame = handle || (t && px >= t.rect.x && px <= t.rect.x + t.rect.w && py >= t.rect.y && py <= t.rect.y + t.rect.h);
    if (!onFrame || (!handle && e.altKey)) {
      const p = this.pick(px, py);
      if (!p) {
        store.clearSelection();
        return;
      }
      if (p.layer || (p.clip.id === store.primary()?.clip.id && p.clip.graphic)) store.selectLayer(p.clip.id, p.layer);
      else store.select([p.clip.id]);
      this.sync();
      t = this.target();
      if (!t) return;
    }
    const fixed = store.readOnly || !!t!.layer?.pin || !!(t!.layer ?? t!.clip).locked;
    if (fixed) return;
    this.drag(e, t!, handle ?? "move");
  }

  private drag(e: PointerEvent, t: Target, handle: Handle): void {
    e.preventDefault();
    this.el.setPointerCapture(e.pointerId);
    const [sx, sy] = this.norm(e);
    const clipId = t.clip.id;
    const layerId = t.layer?.id ?? null;
    const base = [...(t.layer ?? t.clip).box];
    // canvas-normalized → box-space scale (layers live inside the graphic's box)
    const gr = layerId ? this.clipRect(t.clip, store.playhead) : { x: 0, y: 0, w: 1, h: 1 };
    const s = layerId ? 1 : prop(t.clip, "scale", store.playhead - t.clip.start, t.clip.transform.scale) || 1;
    const kx = 1 / (gr.w * (layerId ? 1 : s));
    const ky = 1 / (gr.h * (layerId ? 1 : s));
    const label = handle === "move" ? "Move in monitor" : "Resize in monitor";
    const key = `xf:${clipId}:${layerId ?? ""}:${Date.now()}`;
    const move = (ev: PointerEvent) => {
      const [x, y] = this.norm(ev);
      let dx = (x - sx) * (handle === "move" ? 1 / gr.w : kx);
      let dy = (y - sy) * (handle === "move" ? 1 / gr.h : ky);
      let [bx, by, bw, bh] = base as [number, number, number, number];
      if (handle === "move") {
        if (ev.shiftKey) Math.abs(dx) > Math.abs(dy) ? (dy = 0) : (dx = 0);
        bx += dx;
        by += dy;
        // snap the box centre to the frame centre
        const cx = bx + bw / 2;
        const cy = by + bh / 2;
        if (Math.abs(cx - 0.5) < 0.008) bx = 0.5 - bw / 2;
        if (Math.abs(cy - 0.5) < 0.008) by = 0.5 - bh / 2;
      } else {
        const ar = bw / bh;
        if (handle.includes("w")) [bx, bw] = [bx + dx, bw - dx];
        if (handle.includes("e")) bw += dx;
        if (handle.includes("n")) [by, bh] = [by + dy, bh - dy];
        if (handle.includes("s")) bh += dy;
        if (ev.shiftKey && handle.length === 2) {
          const nh = bw / ar;
          if (handle.includes("n")) by += bh - nh;
          bh = nh;
        }
        bw = Math.max(0.01, bw);
        bh = Math.max(0.005, bh);
      }
      const box = [r4(bx), r4(by), r4(bw), r4(bh)];
      store.apply(label, (tl) => (layerId ? gfx.updateLayer(tl, clipId, layerId, (l) => (l.box = box)) : ops.updateClip(tl, clipId, (c) => (c.box = box))), { coalesce: key });
    };
    const up = () => {
      this.el.removeEventListener("pointermove", move);
      this.el.removeEventListener("pointerup", up);
      this.el.removeEventListener("pointercancel", up);
    };
    this.el.addEventListener("pointermove", move);
    this.el.addEventListener("pointerup", up);
    this.el.addEventListener("pointercancel", up);
  }

  private dbl(e: MouseEvent): void {
    const [px, py] = this.norm(e);
    const p = this.pick(px, py);
    if (p?.clip.graphic) {
      store.selectLayer(p.clip.id, p.layer);
      store.showPanel("graphics");
    }
  }

  private key(e: KeyboardEvent): void {
    const t = this.target();
    if (!t || store.readOnly || !e.key.startsWith("Arrow")) return;
    e.preventDefault();
    e.stopPropagation();
    const step = e.shiftKey ? 0.01 : 0.001;
    const dx = e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0;
    const dy = e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0;
    const nudge = (b: number[]) => [r4(b[0]! + dx), r4(b[1]! + dy), b[2]!, b[3]!];
    const lid = t.layer?.id;
    store.apply("Nudge in monitor", (tl) => (lid ? gfx.updateLayer(tl, t.clip.id, lid, (l) => (l.box = nudge(l.box))) : ops.updateClip(tl, t.clip.id, (c) => (c.box = nudge(c.box)))), { coalesce: `nudge:${t.clip.id}:${lid ?? ""}` });
  }
}

const r4 = (v: number) => Math.round(v * 10000) / 10000;
