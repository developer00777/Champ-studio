// Mirror of engine/champstudio/timeline.py — the canonical timeline document.
// Time is integer frames at `fps`; `in` is the source offset in frames;
// keyframe `f` is relative to clip start; `box` is normalized [x, y, w, h];
// transform x/y are offsets in canvas widths/heights. Video tracks composite
// in list order (first = bottom, like V1).

export type Ease = "linear" | "ease" | "ease-in" | "ease-out" | "hold";

export interface Keyframe {
  f: number;
  v: number;
  ease: Ease;
}

export interface Anim {
  type: string;
  duration: number;
}

export interface Transform {
  x: number;
  y: number;
  scale: number;
  rotation: number;
  opacity: number;
}

export interface TextStyle {
  font: string;
  weight: number;
  size: number;
  min_size: number | null;
  color: string;
  align: "left" | "center" | "right";
  valign: "top" | "middle" | "bottom";
  line_height: number;
  letter_spacing: number;
  uppercase: boolean;
  fit: "shrink" | "none";
  stroke_color: string | null;
  stroke_width: number;
  shadow: boolean;
  shadow_color: string;
  bg_color: string | null;
  bg_padding: number;
  bg_radius: number;
  highlight_color: string | null;
  highlight_bg: string | null;
  [extra: string]: unknown;
}

export interface Shape {
  kind: "rect" | "ellipse" | "line";
  fill: string | null;
  radius: number;
  stroke: string | null;
  stroke_width: number;
  gradient: string[] | null;
}

export interface Adjust {
  exposure: number;
  contrast: number;
  saturation: number;
  temperature: number;
  tint: number;
  brightness: number;
  lut_asset_id: string | null;
  lut_strength: number;
}

export interface CaptionWord {
  w: string;
  s: number; // absolute timeline frames
  e: number;
}

export interface Captions {
  words: CaptionWord[];
  mode: "line" | "karaoke" | "word";
  max_chars: number;
  max_lines: number;
}

export type ClipType = "video" | "image" | "text" | "shape" | "color" | "audio" | "captions" | "adjustment";
export type AudioRole = "dialogue" | "vo" | "music" | "sfx" | "ambience";
export type Fit = "cover" | "contain" | "fill" | "reframe";

export interface Clip {
  id: string;
  type: ClipType;
  start: number;
  duration: number;
  in: number;
  asset_id: string | null;
  src: string | null;
  name: string;
  enabled: boolean;
  box: number[];
  fit: Fit;
  transform: Transform;
  keyframes: Record<string, Keyframe[]>;
  anim_in: Anim | null;
  anim_out: Anim | null;
  transition_in: Anim | null;
  speed: number;
  reverse: boolean;
  freeze: boolean;
  text: string;
  style: TextStyle;
  shape: Shape;
  color: string;
  adjust: Adjust | null;
  captions: Captions | null;
  volume: number;
  fade_in: number;
  fade_out: number;
  role: AudioRole | null;
  duck_db: number | null;
  linked: string | null;
  source: Record<string, unknown>;
  nest: Timeline | null;
  [extra: string]: unknown;
}

export type TrackKind = "video" | "audio";

export interface Track {
  id: string;
  kind: TrackKind;
  name: string;
  clips: Clip[];
  muted: boolean;
  hidden: boolean;
  locked: boolean;
  solo: boolean;
  volume: number;
}

export interface Marker {
  f: number;
  label: string;
  color: string;
  duration: number;
}

export interface Timeline {
  version: number;
  fps: number;
  width: number;
  height: number;
  background: string;
  tracks: Track[];
  markers: Marker[];
  meta: Record<string, unknown>;
  loudness: number | null;
  [extra: string]: unknown;
}

// ── defaults (match pydantic defaults) ───────────────────────────────

export const defaultTransform = (): Transform => ({ x: 0, y: 0, scale: 1, rotation: 0, opacity: 1 });

export const defaultTextStyle = (): TextStyle => ({
  font: "Inter",
  weight: 700,
  size: 64,
  min_size: null,
  color: "#FFFFFF",
  align: "center",
  valign: "middle",
  line_height: 1.15,
  letter_spacing: 0,
  uppercase: false,
  fit: "shrink",
  stroke_color: null,
  stroke_width: 0,
  shadow: false,
  shadow_color: "#000000AA",
  bg_color: null,
  bg_padding: 0.35,
  bg_radius: 0.25,
  highlight_color: null,
  highlight_bg: null,
});

export const defaultShape = (): Shape => ({
  kind: "rect",
  fill: "#FFFFFF",
  radius: 0,
  stroke: null,
  stroke_width: 0,
  gradient: null,
});

export const defaultAdjust = (): Adjust => ({
  exposure: 0,
  contrast: 0,
  saturation: 1,
  temperature: 0,
  tint: 0,
  brightness: 0,
  lut_asset_id: null,
  lut_strength: 1,
});

let idCounter = 0;
export function newId(prefix = "c"): string {
  idCounter = (idCounter + 1) % 1e6;
  const rnd = Math.random().toString(36).slice(2, 8);
  return `${prefix}_${Date.now().toString(36)}${idCounter.toString(36)}${rnd}`;
}

export function makeClip(partial: Partial<Clip> & { type: ClipType }): Clip {
  const base: Clip = {
    id: newId("c"),
    type: partial.type,
    start: 0,
    duration: 30,
    in: 0,
    asset_id: null,
    src: null,
    name: "",
    enabled: true,
    box: [0, 0, 1, 1],
    fit: "cover",
    transform: defaultTransform(),
    keyframes: {},
    anim_in: null,
    anim_out: null,
    transition_in: null,
    speed: 1,
    reverse: false,
    freeze: false,
    text: "",
    style: defaultTextStyle(),
    shape: defaultShape(),
    color: "#000000",
    adjust: null,
    captions: null,
    volume: 1,
    fade_in: 0,
    fade_out: 0,
    role: null,
    duck_db: null,
    linked: null,
    source: {},
    nest: null,
  };
  return normalizeClip({ ...base, ...partial } as Clip);
}

/** Fill any missing fields (documents from older engines / templates). */
export function normalizeClip(c: Clip): Clip {
  const raw = c as Partial<Clip> & Clip;
  const out: Clip = {
    ...raw,
    start: Math.round(raw.start ?? 0),
    duration: Math.max(1, Math.round(raw.duration ?? 30)),
    in: Math.round(raw.in ?? 0),
    asset_id: raw.asset_id ?? null,
    src: raw.src ?? null,
    name: raw.name ?? "",
    enabled: raw.enabled ?? true,
    box: Array.isArray(raw.box) && raw.box.length >= 4 ? raw.box.slice(0, 4) : [0, 0, 1, 1],
    fit: raw.fit ?? "cover",
    transform: { ...defaultTransform(), ...(raw.transform ?? {}) },
    keyframes: raw.keyframes ?? {},
    anim_in: raw.anim_in ?? null,
    anim_out: raw.anim_out ?? null,
    transition_in: raw.transition_in ?? null,
    speed: raw.speed && raw.speed > 0 ? raw.speed : 1,
    reverse: raw.reverse ?? false,
    freeze: raw.freeze ?? false,
    text: raw.text ?? "",
    style: { ...defaultTextStyle(), ...(raw.style ?? {}) },
    shape: { ...defaultShape(), ...(raw.shape ?? {}) },
    color: raw.color ?? "#000000",
    adjust: raw.adjust ? { ...defaultAdjust(), ...raw.adjust } : null,
    captions: raw.captions ?? null,
    volume: raw.volume ?? 1,
    fade_in: raw.fade_in ?? 0,
    fade_out: raw.fade_out ?? 0,
    role: raw.role ?? null,
    duck_db: raw.duck_db ?? null,
    linked: raw.linked ?? null,
    source: raw.source ?? {},
    nest: raw.nest ?? null,
  };
  return out;
}

export function makeTrack(kind: TrackKind, name: string, id?: string): Track {
  return { id: id ?? newId(kind === "video" ? "v" : "a"), kind, name, clips: [], muted: false, hidden: false, locked: false, solo: false, volume: 1 };
}

export function normalizeTimeline(raw: Partial<Timeline> | null | undefined): Timeline {
  const r = raw ?? {};
  const tracks = (r.tracks ?? []).map((t) => ({
    ...makeTrack(t.kind, t.name ?? "", t.id),
    ...t,
    clips: (t.clips ?? []).map((c) => normalizeClip(c)),
  }));
  return {
    ...r,
    version: r.version ?? 1,
    fps: r.fps ?? 30,
    width: r.width ?? 1920,
    height: r.height ?? 1080,
    background: r.background ?? "#000000",
    tracks,
    markers: (r.markers ?? []).map((m) => ({ f: Math.round(m.f), label: m.label ?? "", color: m.color ?? "#FF6B35", duration: m.duration ?? 0 })),
    meta: r.meta ?? {},
    loudness: r.loudness === undefined ? -14 : r.loudness,
  };
}

// ── evaluation helpers (port of timeline.py) ─────────────────────────

export const clipEnd = (c: Clip): number => c.start + c.duration;

export function ease(t: number, kind: Ease | string): number {
  t = Math.min(1, Math.max(0, t));
  if (kind === "linear") return t;
  if (kind === "hold") return 0;
  if (kind === "ease-in") return t * t * t;
  if (kind === "ease-out") return 1 - (1 - t) ** 3;
  return t * t * (3 - 2 * t);
}

export function evalKeyframes(kfs: Keyframe[] | undefined, f: number, def: number): number {
  if (!kfs || kfs.length === 0) return def;
  const s = [...kfs].sort((a, b) => a.f - b.f);
  const first = s[0]!;
  const last = s[s.length - 1]!;
  if (f <= first.f) return first.v;
  if (f >= last.f) return last.v;
  for (let i = 0; i < s.length - 1; i++) {
    const a = s[i]!;
    const b = s[i + 1]!;
    if (a.f <= f && f <= b.f) {
      const span = Math.max(1, b.f - a.f);
      return a.v + (b.v - a.v) * ease((f - a.f) / span, a.ease);
    }
  }
  return def;
}

export function prop(clip: Clip, name: string, fRel: number, def: number): number {
  return evalKeyframes(clip.keyframes[name], fRel, def);
}

export const ASPECTS: Record<string, [number, number]> = {
  "16:9": [1920, 1080],
  "9:16": [1080, 1920],
  "1:1": [1080, 1080],
  "4:5": [1080, 1350],
  "4:3": [1440, 1080],
};

export function aspectOf(tl: { width: number; height: number }): string {
  const r = tl.width / tl.height;
  let best = "16:9";
  let bestD = Infinity;
  for (const [k, [w, h]] of Object.entries(ASPECTS)) {
    const d = Math.abs(w / h - r);
    if (d < bestD) {
      bestD = d;
      best = k;
    }
  }
  return best;
}

export function secondsToFrames(sec: number, fps: number): number {
  return Math.floor(sec * fps + 0.5);
}

export function timecode(frames: number, fps: number): string {
  const neg = frames < 0;
  let f = Math.abs(Math.round(frames));
  const ff = f % fps;
  f = Math.floor(f / fps);
  const ss = f % 60;
  f = Math.floor(f / 60);
  const mm = f % 60;
  const hh = Math.floor(f / 60);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${neg ? "-" : ""}${p(hh)}:${p(mm)}:${p(ss)}:${p(ff)}`;
}

/** Parse "HH:MM:SS:FF", "MM:SS:FF", "SS:FF", "1.5s" or plain frames. */
export function parseTimecode(s: string, fps: number): number | null {
  const t = s.trim();
  if (!t) return null;
  if (/^-?\d+(\.\d+)?s$/.test(t)) return secondsToFrames(parseFloat(t), fps);
  if (/^-?\d+$/.test(t)) return parseInt(t, 10);
  const parts = t.split(/[:;.]/).map((p) => parseInt(p, 10));
  if (parts.some((p) => Number.isNaN(p))) return null;
  while (parts.length < 4) parts.unshift(0);
  const [h, m, sec, f] = parts as [number, number, number, number];
  return ((h * 60 + m) * 60 + sec) * fps + f;
}

export function timelineDuration(tl: Timeline): number {
  let end = 0;
  for (const t of tl.tracks) for (const c of t.clips) if (c.enabled) end = Math.max(end, clipEnd(c));
  return end;
}

export function findClip(tl: Timeline, id: string): { clip: Clip; track: Track; index: number } | null {
  for (const track of tl.tracks) {
    const index = track.clips.findIndex((c) => c.id === id);
    if (index >= 0) return { clip: track.clips[index]!, track, index };
  }
  return null;
}

export function cloneTimeline(tl: Timeline): Timeline {
  return structuredClone(tl);
}

export function isMediaClip(c: Clip): boolean {
  return (c.type === "video" || c.type === "audio") && !c.freeze && !c.nest;
}
