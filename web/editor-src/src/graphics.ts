// Motion graphics (Essential Graphics / MOGRT). Mirror of
// engine/champstudio/graphics.py so the realtime preview matches the export:
// control bindings, layer timing, number counters and per-unit text animators.
// Also the pure layer operations used by the Essential Graphics panel.

import { cloneTimeline, ease, findClip, makeClip, newId, normalizeClip, type Clip, type ClipType, type Control, type Counter, type TextAnim, type Timeline } from "./model";

const PLACEHOLDER = /\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g;
/** "{{percent}} * 0.006 + 0.1": a control scaled into a layer number (bar lengths, positions). */
const ARITH = /^\s*\{\{\s*([a-zA-Z0-9_]+)\s*\}\}((?:\s*[*/+-]\s*-?\d+(?:\.\d+)?)+)\s*$/;
const OP = /([*/+-])\s*(-?\d+(?:\.\d+)?)/g;
const NUMBER = /\d[\d,]*(?:\.\d+)?/;
const NUMERIC = new Set(["size", "weight", "line_height", "letter_spacing", "stroke_width", "radius", "x", "y", "scale", "rotation", "opacity", "bg_padding", "bg_radius", "gradient_angle", "sides", "volume"]);
const BOOLEAN = new Set(["enabled", "uppercase", "shadow", "hold"]);

// ── controls / bindings ──────────────────────────────────────────────

export function controlValues(clip: Clip): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const c of clip.graphic?.controls ?? []) out[c.id] = c.value;
  return out;
}

function truthy(v: unknown): boolean {
  if (typeof v === "string") return !["", "0", "false", "no", "off"].includes(v.trim().toLowerCase());
  return !!v;
}

function toNumber(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(String(v).replace(/,/g, "").trim().replace(/%$/, ""));
  return Number.isNaN(n) ? null : n;
}

function boundValue(template: string, values: Record<string, unknown>, path: string): unknown {
  const arith = ARITH.exec(template);
  if (arith) {
    let v = toNumber(values[arith[1]!]);
    if (v === null) return null;
    for (const [, op, ks] of arith[2]!.matchAll(OP)) {
      const k = Number(ks);
      v = op === "*" ? v * k : op === "/" ? (k ? v / k : v) : op === "+" ? v + k : v - k;
    }
    return v;
  }
  const whole = /^\s*\{\{\s*([a-zA-Z0-9_]+)\s*\}\}\s*$/.exec(template);
  let v: unknown;
  if (whole) {
    v = values[whole[1]!];
    if (v && typeof v === "object" && "asset_id" in (v as object)) v = (v as { asset_id: string }).asset_id;
  } else v = template.replace(PLACEHOLDER, (_m, k: string) => (values[k] === null || values[k] === undefined ? "" : String(values[k])));
  const leaf = path.split(".").pop()!;
  if (BOOLEAN.has(leaf)) return truthy(v);
  if (NUMERIC.has(leaf) || path.startsWith("box.")) return toNumber(v);
  if (["color", "fill", "stroke", "bg_color", "stroke_color"].includes(leaf) && (v === null || v === undefined || v === "")) return null;
  if (typeof v === "number") return String(v); // number control → text
  return v ?? null;
}

/** Layer with every `bind` path replaced by its control-driven value (unset controls keep the authored value). */
export function applyBindings(layer: Clip, values: Record<string, unknown>): Clip {
  const bind = layer.bind ?? {};
  if (!Object.keys(bind).length) return layer;
  const out = structuredClone(layer) as Clip & Record<string, unknown>;
  for (const [path, template] of Object.entries(bind)) {
    const v = boundValue(String(template), values, path);
    if (v === null) continue;
    if (path.startsWith("box.")) {
      const i = Number(path.split(".")[1]);
      const box = [...(out.box ?? [0, 0, 1, 1])];
      if (i < box.length) {
        box[i] = i >= 2 ? Math.max(0, Number(v)) : Number(v);
        out.box = box;
      }
      continue;
    }
    if (path.startsWith("gradient.")) {
      const i = Number(path.split(".")[1]);
      const g = out.shape.gradient ? [...out.shape.gradient] : null;
      if (g && i < g.length) {
        g[i] = String(v);
        out.shape.gradient = g;
      }
      continue;
    }
    const parts = path.split(".");
    let node = out as Record<string, unknown>;
    for (const p of parts.slice(0, -1)) {
      if (!node[p] || typeof node[p] !== "object") node[p] = {};
      node = node[p] as Record<string, unknown>;
    }
    node[parts[parts.length - 1]!] = v;
  }
  return out;
}

/** Layers with controls applied and timing resolved (frames relative to the graphic start). */
export function resolveLayers(clip: Clip): Clip[] {
  const g = clip.graphic;
  if (!g) return [];
  const values = controlValues(clip);
  const out: Clip[] = [];
  for (const layer of g.layers) {
    const l = applyBindings(layer, values);
    const start = Math.max(0, Math.round(l.start || 0));
    if (start >= clip.duration) continue;
    const duration = l.hold ? clip.duration - start : Math.max(1, Math.min(Math.round(l.duration || 1), clip.duration - start));
    out.push(l === layer ? { ...layer, start, duration } : Object.assign(l, { start, duration }));
  }
  return out;
}

/** Placeholders a layer reads (`{{name}}` → "name"). */
export function boundControls(layer: Clip): string[] {
  const out = new Set<string>();
  for (const t of Object.values(layer.bind ?? {})) for (const m of String(t).matchAll(PLACEHOLDER)) out.add(m[1]!);
  return [...out];
}

// ── counters ─────────────────────────────────────────────────────────

export function counterText(text: string, counter: Counter | null | undefined, fRel: number): string {
  if (!counter) return text;
  const m = NUMBER.exec(text);
  if (!m) return text;
  const raw = m[0];
  const target = Number(raw.replace(/,/g, ""));
  if (Number.isNaN(target)) return text;
  const p = ease((fRel - counter.delay) / Math.max(1, counter.duration), counter.ease);
  const v = counter.start + (target - counter.start) * p;
  const decimals = raw.includes(".") ? raw.split(".")[1]!.length : 0;
  const s = raw.includes(",") ? v.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) : v.toFixed(decimals);
  return text.slice(0, m.index) + s + text.slice(m.index + raw.length);
}

// ── per-unit text animators ──────────────────────────────────────────

function crc32(s: string): number {
  let c = ~0;
  for (let i = 0; i < s.length; i++) {
    c ^= s.charCodeAt(i);
    for (let k = 0; k < 8; k++) c = c & 1 ? (c >>> 1) ^ 0xedb88320 : c >>> 1;
  }
  return ~c >>> 0;
}

export function unitRank(order: TextAnim["order"], i: number, n: number): number {
  if (order === "reverse") return n - 1 - i;
  if (order === "center") return Math.abs(i - (n - 1) / 2);
  if (order === "random") return crc32(`${i}:${n}`) % Math.max(1, n);
  return i;
}

export function unitProgress(anim: TextAnim, i: number, n: number, fRel: number, clipDuration: number): number {
  const r = unitRank(anim.order, i, n);
  let p = Math.min(1, Math.max(0, (fRel - anim.delay - r * anim.stagger + 1) / Math.max(1, anim.duration)));
  if (anim.out) {
    const total = anim.duration + anim.stagger * (n - 1);
    const q = (fRel - (clipDuration - total) - r * anim.stagger + 1) / Math.max(1, anim.duration);
    p = Math.min(p, 1 - Math.min(1, Math.max(0, q)));
  }
  return p;
}

/** [dx px, dy px, scale, opacity, reveal fraction] for one unit at progress p. */
export function unitEffect(kind: string, p: number, cellH: number): [number, number, number, number, number] {
  const e = 1 - (1 - p) ** 3;
  switch (kind) {
    case "fade":
      return [0, 0, 1, p, 0];
    case "rise":
      return [0, (1 - e) * cellH * 0.6, 1, Math.min(1, p * 1.5), 0];
    case "drop":
      return [0, -(1 - e) * cellH * 0.6, 1, Math.min(1, p * 1.5), 0];
    case "pop": {
      const os = p < 1 ? 1 + 0.18 * Math.sin(Math.PI * e) : 1;
      return [0, 0, (0.4 + 0.6 * e) * os, Math.min(1, p * 2), 0];
    }
    case "slide-left":
      return [(1 - e) * cellH * 0.8, 0, 1, Math.min(1, p * 1.5), 0];
    case "slide-right":
      return [-(1 - e) * cellH * 0.8, 0, 1, Math.min(1, p * 1.5), 0];
    case "reveal":
      return [0, 0, 1, p > 0 ? 1 : 0, 1 - e];
    default:
      return [0, 0, 1, p, 0];
  }
}

// ── layer operations (pure; return a new Timeline) ───────────────────

export function findLayer(tl: Timeline, clipId: string, layerId: string): { clip: Clip; layer: Clip; index: number } | null {
  const hit = findClip(tl, clipId);
  const layers = hit?.clip.graphic?.layers;
  if (!hit || !layers) return null;
  const index = layers.findIndex((l) => l.id === layerId);
  return index < 0 ? null : { clip: hit.clip, layer: layers[index]!, index };
}

function editGraphic(tl: Timeline, clipId: string, fn: (clip: Clip) => void): Timeline {
  const out = cloneTimeline(tl);
  const hit = findClip(out, clipId);
  if (!hit?.clip.graphic) return tl;
  fn(hit.clip);
  return out;
}

export function updateLayer(tl: Timeline, clipId: string, layerId: string, fn: (l: Clip) => void): Timeline {
  return editGraphic(tl, clipId, (c) => {
    const l = c.graphic!.layers.find((x) => x.id === layerId);
    if (!l) return;
    fn(l);
    l.start = Math.max(0, Math.round(l.start));
    l.duration = Math.max(1, Math.round(l.duration));
  });
}

export function setLayerKeyframe(tl: Timeline, clipId: string, layerId: string, name: string, fRel: number, v: number): Timeline {
  return updateLayer(tl, clipId, layerId, (l) => {
    const prev = l.keyframes[name] ?? [];
    const list = prev.filter((k) => k.f !== fRel);
    list.push({ f: Math.round(fRel), v, ease: prev.find((k) => k.f === fRel)?.ease ?? "ease" });
    list.sort((a, b) => a.f - b.f);
    l.keyframes = { ...l.keyframes, [name]: list };
  });
}

export function removeLayerKeyframe(tl: Timeline, clipId: string, layerId: string, name: string, fRel: number): Timeline {
  return updateLayer(tl, clipId, layerId, (l) => {
    const list = (l.keyframes[name] ?? []).filter((k) => k.f !== fRel);
    const kf = { ...l.keyframes };
    if (list.length) kf[name] = list;
    else delete kf[name];
    l.keyframes = kf;
  });
}

/** Add a layer on top of the stack. */
export function addLayer(tl: Timeline, clipId: string, layer: Clip): Timeline {
  return editGraphic(tl, clipId, (c) => c.graphic!.layers.push(normalizeClip(layer)));
}

export function removeLayer(tl: Timeline, clipId: string, layerId: string): Timeline {
  return editGraphic(tl, clipId, (c) => {
    const g = c.graphic!;
    g.layers = g.layers.filter((l) => l.id !== layerId);
    for (const l of g.layers) if (l.pin?.to === layerId) l.pin = null;
  });
}

export function duplicateLayer(tl: Timeline, clipId: string, layerId: string, newLayerId = newId("l")): Timeline {
  return editGraphic(tl, clipId, (c) => {
    const g = c.graphic!;
    const i = g.layers.findIndex((l) => l.id === layerId);
    if (i < 0) return;
    const copy = structuredClone(g.layers[i]!);
    copy.id = newLayerId;
    copy.name = `${copy.name || copy.type} copy`;
    copy.box = [copy.box[0]! + 0.02, copy.box[1]! + 0.02, copy.box[2]!, copy.box[3]!];
    g.layers.splice(i + 1, 0, copy);
  });
}

/** Move a layer up (+1, toward the top of the stack) or down (-1). */
export function moveLayer(tl: Timeline, clipId: string, layerId: string, dir: 1 | -1): Timeline {
  return editGraphic(tl, clipId, (c) => {
    const L = c.graphic!.layers;
    const i = L.findIndex((l) => l.id === layerId);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= L.length) return;
    [L[i], L[j]] = [L[j]!, L[i]!];
  });
}

export function reorderLayer(tl: Timeline, clipId: string, layerId: string, toIndex: number): Timeline {
  return editGraphic(tl, clipId, (c) => {
    const L = c.graphic!.layers;
    const i = L.findIndex((l) => l.id === layerId);
    if (i < 0) return;
    const [l] = L.splice(i, 1);
    L.splice(Math.max(0, Math.min(L.length, toIndex)), 0, l!);
  });
}

export function setControlValue(tl: Timeline, clipId: string, controlId: string, value: Control["value"]): Timeline {
  return editGraphic(tl, clipId, (c) => {
    const ctl = c.graphic!.controls.find((k) => k.id === controlId);
    if (ctl) ctl.value = value;
  });
}

export function updateControl(tl: Timeline, clipId: string, controlId: string, fn: (k: Control) => void): Timeline {
  return editGraphic(tl, clipId, (c) => {
    const ctl = c.graphic!.controls.find((k) => k.id === controlId);
    if (ctl) fn(ctl);
  });
}

export function removeControl(tl: Timeline, clipId: string, controlId: string): Timeline {
  return editGraphic(tl, clipId, (c) => {
    const g = c.graphic!;
    const ctl = g.controls.find((k) => k.id === controlId);
    g.controls = g.controls.filter((k) => k.id !== controlId);
    // bake the current value into the layers that read it, then drop the binding
    const values = { [controlId]: ctl?.value };
    g.layers = g.layers.map((l) => {
      const reads = Object.entries(l.bind).filter(([, t]) => new RegExp(`\\{\\{\\s*${controlId}\\s*\\}\\}`).test(t));
      if (!reads.length) return l;
      const baked = applyBindings({ ...l, bind: Object.fromEntries(reads) }, values);
      const bind = Object.fromEntries(Object.entries(l.bind).filter(([p]) => !reads.some(([rp]) => rp === p)));
      return { ...baked, bind };
    });
  });
}

/** Expose a layer property as a new control (Premiere: "Add to Master Properties"). */
export function exposeProperty(tl: Timeline, clipId: string, layerId: string, path: string, kind: Control["kind"], label: string): { tl: Timeline; controlId: string } {
  const hit = findLayer(tl, clipId, layerId);
  if (!hit) return { tl, controlId: "" };
  const taken = new Set(hit.clip.graphic!.controls.map((k) => k.id));
  const base = (label || path).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 32) || "control";
  let id = base;
  for (let n = 2; taken.has(id); n++) id = `${base}_${n}`;
  const value = readPath(hit.layer, path);
  const out = editGraphic(tl, clipId, (c) => {
    c.graphic!.controls.push({ id, label, kind, value: (value ?? "") as Control["value"], options: [] });
    const l = c.graphic!.layers.find((x) => x.id === layerId)!;
    l.bind = { ...l.bind, [path]: `{{${id}}}` };
  });
  return { tl: out, controlId: id };
}

export function readPath(obj: unknown, path: string): unknown {
  if (path.startsWith("gradient.")) return (obj as Clip).shape?.gradient?.[Number(path.split(".")[1])] ?? null;
  let node: unknown = obj;
  for (const p of path.split(".")) {
    if (!node || typeof node !== "object") return undefined;
    node = (node as Record<string, unknown>)[p];
  }
  return node;
}

/** A new layer of the given kind, centred in the frame. */
export function makeLayer(kind: "text" | "rect" | "ellipse" | "line" | "triangle" | "polygon" | "star" | "image" | "color", opts: { accent?: string; asset_id?: string; text?: string } = {}): Clip {
  const type: ClipType = kind === "text" ? "text" : kind === "image" ? "image" : kind === "color" ? "color" : "shape";
  const l = makeClip({ type, id: newId("l"), start: 0, duration: 30 });
  l.hold = true;
  if (type === "text") {
    l.name = "Text";
    l.text = opts.text ?? "New text";
    l.box = [0.2, 0.42, 0.6, 0.16];
  } else if (type === "image") {
    l.name = "Image";
    l.asset_id = opts.asset_id ?? null;
    l.fit = "contain";
    l.box = [0.35, 0.3, 0.3, 0.4];
  } else if (type === "color") {
    l.name = "Background";
    l.color = "#00000099";
    l.box = [0, 0, 1, 1];
  } else {
    l.name = kind[0]!.toUpperCase() + kind.slice(1);
    l.shape.kind = kind as Clip["shape"]["kind"];
    l.shape.fill = opts.accent ?? "#FF6B35";
    l.shape.radius = kind === "rect" ? 0.15 : 0;
    l.box = kind === "line" ? [0.3, 0.6, 0.4, 0.008] : kind === "rect" ? [0.3, 0.4, 0.4, 0.2] : [0.42, 0.38, 0.16, 0.24];
  }
  return l;
}

/** A new, empty graphic clip (Premiere: Graphics → New Layer creates one). */
export function makeGraphic(start: number, duration: number, first?: Clip): Clip {
  const c = makeClip({ type: "graphic", name: "Graphic", start, duration, box: [0, 0, 1, 1] });
  c.graphic = { layers: first ? [first] : [], controls: [], template: null };
  return c;
}

/** Group loose visual clips into one graphic (their timing and boxes are kept). */
export function groupIntoGraphic(tl: Timeline, ids: string[], graphicId = newId("g")): { tl: Timeline; id: string | null } {
  const hits = ids.map((id) => findClip(tl, id)).filter((h): h is NonNullable<typeof h> => !!h && ["text", "shape", "image", "color", "video"].includes(h.clip.type) && !h.clip.nest);
  if (!hits.length) return { tl, id: null };
  const order = new Map(tl.tracks.map((t, i) => [t.id, i]));
  hits.sort((a, b) => order.get(a.track.id)! - order.get(b.track.id)! || a.clip.start - b.clip.start);
  const start = Math.min(...hits.map((h) => h.clip.start));
  const end = Math.max(...hits.map((h) => h.clip.start + h.clip.duration));
  const layers = hits.map((h) => {
    const l = structuredClone(h.clip);
    l.start -= start;
    l.hold = l.start + l.duration >= end - start;
    l.linked = null;
    return l;
  });
  const g = makeGraphic(start, end - start);
  g.id = graphicId;
  g.graphic!.layers = layers;
  const out = cloneTimeline(tl);
  const drop = new Set(hits.map((h) => h.clip.id));
  for (const t of out.tracks) t.clips = t.clips.filter((c) => !drop.has(c.id));
  const bottom = out.tracks.find((t) => t.id === hits[0]!.track.id)!;
  bottom.clips.push(g);
  bottom.clips.sort((a, b) => a.start - b.start);
  return { tl: out, id: g.id };
}

/** Bake controls into the layers and lay them out as loose clips on tracks above. */
export function ungroupGraphic(tl: Timeline, clipId: string): { tl: Timeline; ids: string[] } {
  const hit = findClip(tl, clipId);
  if (!hit?.clip.graphic) return { tl, ids: [] };
  const clip = hit.clip;
  const layers = resolveLayers(clip).map((l) => ({ ...l, start: l.start + clip.start, id: newId("c"), bind: {}, pin: null, mask: null }));
  let out = cloneTimeline(tl);
  for (const t of out.tracks) t.clips = t.clips.filter((c) => c.id !== clipId);
  const vids = out.tracks.filter((t) => t.kind === "video");
  let ti = vids.findIndex((t) => t.id === hit.track.id);
  const ids: string[] = [];
  for (const l of layers) {
    let track = vids[ti];
    while (track && track.clips.some((c) => c.start < l.start + l.duration && c.start + c.duration > l.start)) track = vids[++ti];
    if (!track) {
      const idx = out.tracks.lastIndexOf(vids[vids.length - 1]!) + 1;
      track = { id: newId("v"), kind: "video", name: `V${vids.length + 1}`, clips: [], muted: false, hidden: false, locked: false, solo: false, volume: 1 };
      out.tracks.splice(idx, 0, track);
      vids.push(track);
      ti = vids.length - 1;
    }
    track.clips.push(normalizeClip(l));
    track.clips.sort((a, b) => a.start - b.start);
    ids.push(l.id);
    ti++;
  }
  out = { ...out };
  return { tl: out, ids };
}
