// Pure timeline operations. Every function takes a Timeline and returns a NEW
// Timeline (the input is never mutated), so the store can keep before/after
// snapshots for undo. All frame math stays in exact integers.

import { clipEnd, cloneTimeline, findClip, newId, type Clip, type Marker, type Timeline, type Track, type TrackKind, makeTrack } from "./model";

export interface OpCtx {
  /** Length of the clip's source in timeline frames (undefined = unlimited). */
  sourceFrames?: (clip: Clip) => number | undefined;
}

const usesSource = (c: Clip): boolean => (c.type === "video" || c.type === "audio") && !c.freeze;

function sortTrack(t: Track): void {
  t.clips.sort((a, b) => a.start - b.start || a.id.localeCompare(b.id));
}

/** Move a clip in time (captions words are absolute and travel with it). */
function shiftClip(c: Clip, d: number): void {
  if (!d) return;
  c.start += d;
  if (c.captions?.words) {
    for (const w of c.captions.words) {
      w.s += d;
      w.e += d;
    }
  }
}

function shiftKeyframes(c: Clip, d: number): void {
  if (!d) return;
  for (const k of Object.keys(c.keyframes)) {
    c.keyframes[k] = (c.keyframes[k] ?? []).map((kf) => ({ ...kf, f: kf.f + d }));
  }
}

/** Source frames consumed for `frames` of timeline time. */
const srcFrames = (c: Clip, frames: number): number => Math.round(frames * c.speed);

/** Split a clip object at absolute frame `f` (start < f < end). Returns [left, right]. */
export function splitClipObject(c: Clip, f: number, rightId = newId("c")): [Clip, Clip] {
  const rel = f - c.start;
  const left: Clip = structuredClone(c);
  const right: Clip = structuredClone(c);
  left.duration = rel;
  left.anim_out = null;
  left.fade_out = 0;
  right.id = rightId;
  right.start = f;
  right.duration = c.duration - rel;
  if (!c.freeze) right.in = c.in + srcFrames(c, rel);
  right.anim_in = null;
  right.transition_in = null;
  right.fade_in = 0;
  // Keyframes are kept whole on both halves (shifted on the right) so the
  // animation curve is evaluated identically on either side of the cut.
  shiftKeyframes(right, -rel);
  return [left, right];
}

function linkMapper(): (link: string | null) => string | null {
  const map = new Map<string, string>();
  return (link) => {
    if (!link) return null;
    let v = map.get(link);
    if (!v) {
      v = newId("lk");
      map.set(link, v);
    }
    return v;
  };
}

/**
 * Add an edit at frame `f`. Splits every clip crossing `f` on the given tracks
 * (or only the given clips). Right halves of linked clips share a new link id.
 */
export function splitAt(tl: Timeline, f: number, opts: { trackIds?: string[]; clipIds?: string[] } = {}): Timeline {
  const out = cloneTimeline(tl);
  const relink = linkMapper();
  for (const track of out.tracks) {
    if (track.locked) continue;
    if (opts.trackIds && !opts.trackIds.includes(track.id)) continue;
    const next: Clip[] = [];
    for (const c of track.clips) {
      const hit = c.start < f && f < clipEnd(c) && (!opts.clipIds || opts.clipIds.includes(c.id));
      if (!hit) {
        next.push(c);
        continue;
      }
      const [l, r] = splitClipObject(c, f);
      r.linked = relink(c.linked);
      next.push(l, r);
    }
    track.clips = next;
    sortTrack(track);
  }
  return out;
}

/** Remove clips, leaving a gap. */
export function lift(tl: Timeline, ids: Iterable<string>): Timeline {
  const set = new Set(ids);
  const out = cloneTimeline(tl);
  for (const t of out.tracks) t.clips = t.clips.filter((c) => !set.has(c.id));
  return out;
}

function mergeIntervals(iv: Array<[number, number]>): Array<[number, number]> {
  const s = [...iv].sort((a, b) => a[0] - b[0]);
  const out: Array<[number, number]> = [];
  for (const [a, b] of s) {
    const last = out[out.length - 1];
    if (last && a <= last[1]) last[1] = Math.max(last[1], b);
    else out.push([a, b]);
  }
  return out;
}

/** Amount by which removed intervals before `f` shorten the track. */
function removedBefore(iv: Array<[number, number]>, f: number): number {
  let d = 0;
  for (const [a, b] of iv) d += Math.max(0, Math.min(b, f) - a);
  return d;
}

/** Delete clips and close the gaps on their tracks. */
export function rippleDelete(tl: Timeline, ids: Iterable<string>): Timeline {
  const set = new Set(ids);
  const out = cloneTimeline(tl);
  for (const t of out.tracks) {
    const removed = t.clips.filter((c) => set.has(c.id));
    if (!removed.length) continue;
    const iv = mergeIntervals(removed.map((c) => [c.start, clipEnd(c)] as [number, number]));
    // a gap only exists where nothing else on the track still plays
    const keep = t.clips.filter((c) => !set.has(c.id));
    const gaps = subtractIntervals(iv, keep.map((c) => [c.start, clipEnd(c)] as [number, number]));
    t.clips = keep;
    for (const c of keep) shiftClip(c, -removedBefore(gaps, c.start));
    sortTrack(t);
  }
  return out;
}

function subtractIntervals(a: Array<[number, number]>, b: Array<[number, number]>): Array<[number, number]> {
  let cur = a.map((x) => [...x] as [number, number]);
  for (const [bs, be] of b) {
    const next: Array<[number, number]> = [];
    for (const [s, e] of cur) {
      if (be <= s || bs >= e) next.push([s, e]);
      else {
        if (bs > s) next.push([s, bs]);
        if (be < e) next.push([be, e]);
      }
    }
    cur = next;
  }
  return cur;
}

/** Ripple-delete the time range [from, to) on the given tracks (splitting clips as needed). */
export function rippleDeleteRange(tl: Timeline, from: number, to: number, trackIds: string[]): Timeline {
  if (to <= from) return tl;
  let out = splitAt(tl, from, { trackIds });
  out = splitAt(out, to, { trackIds });
  const ids: string[] = [];
  for (const t of out.tracks) {
    if (!trackIds.includes(t.id) || t.locked) continue;
    for (const c of t.clips) if (c.start >= from && clipEnd(c) <= to) ids.push(c.id);
  }
  out = lift(out, ids);
  for (const t of out.tracks) {
    if (!trackIds.includes(t.id) || t.locked) continue;
    for (const c of t.clips) if (c.start >= to) shiftClip(c, -(to - from));
    sortTrack(t);
  }
  return out;
}

/** Make [from, to) empty on a track, trimming/splitting/removing clips that overlap it. */
function clearRange(track: Track, from: number, to: number, keep: Set<string>, relink: (l: string | null) => string | null): void {
  const next: Clip[] = [];
  for (const c of track.clips) {
    const s = c.start;
    const e = clipEnd(c);
    if (keep.has(c.id) || e <= from || s >= to) {
      next.push(c);
      continue;
    }
    if (s >= from && e <= to) continue; // fully covered → removed
    if (s < from && e > to) {
      const [l, r0] = splitClipObject(c, from);
      const [, r] = splitClipObject(r0, to, r0.id);
      r.linked = relink(c.linked);
      next.push(l, r);
      continue;
    }
    if (s < from) {
      const [l] = splitClipObject(c, from);
      next.push(l);
      continue;
    }
    const [, r] = splitClipObject(c, to, c.id);
    next.push(r);
  }
  track.clips = next;
}

/** Place clips (overwrite edit): whatever is under them is trimmed away. */
export function overwrite(tl: Timeline, placements: Array<{ trackId: string; clip: Clip }>): Timeline {
  const out = cloneTimeline(tl);
  const relink = linkMapper();
  for (const p of placements) {
    const t = out.tracks.find((x) => x.id === p.trackId);
    if (!t) continue;
    clearRange(t, p.clip.start, clipEnd(p.clip), new Set(), relink);
    t.clips.push(structuredClone(p.clip));
    sortTrack(t);
  }
  return out;
}

/** Insert edit: split at `at` and push everything after it right by the inserted length. */
export function insert(tl: Timeline, placements: Array<{ trackId: string; clip: Clip }>, at: number): Timeline {
  if (!placements.length) return tl;
  const len = Math.max(...placements.map((p) => p.clip.duration));
  const trackIds = [...new Set(placements.map((p) => p.trackId))];
  const out = splitAt(tl, at, { trackIds });
  for (const t of out.tracks) {
    if (!trackIds.includes(t.id)) continue;
    for (const c of t.clips) if (c.start >= at) shiftClip(c, len);
  }
  for (const p of placements) {
    const t = out.tracks.find((x) => x.id === p.trackId);
    if (!t) continue;
    const c = structuredClone(p.clip);
    shiftClip(c, at - c.start);
    t.clips.push(c);
    sortTrack(t);
  }
  return out;
}

/**
 * Move clips by `delta` frames and optionally onto other tracks
 * (`trackMap[srcTrackId] = dstTrackId`). Moved clips overwrite what they land on.
 */
export function moveClips(tl: Timeline, ids: Iterable<string>, delta: number, trackMap: Record<string, string> = {}): Timeline {
  const set = new Set(ids);
  const out = cloneTimeline(tl);
  const moving: Array<{ clip: Clip; dst: string }> = [];
  for (const t of out.tracks) {
    for (const c of t.clips) if (set.has(c.id)) moving.push({ clip: c, dst: trackMap[t.id] ?? t.id });
    t.clips = t.clips.filter((c) => !set.has(c.id));
  }
  if (!moving.length) return tl;
  const minStart = Math.min(...moving.map((m) => m.clip.start));
  const d = Math.max(delta, -minStart);
  const relink = linkMapper();
  for (const m of moving) {
    shiftClip(m.clip, d);
    const t = out.tracks.find((x) => x.id === m.dst);
    if (t) clearRange(t, m.clip.start, clipEnd(m.clip), set, relink);
  }
  for (const m of moving) {
    const t = out.tracks.find((x) => x.id === m.dst);
    t?.clips.push(m.clip);
  }
  for (const t of out.tracks) sortTrack(t);
  return out;
}

/** Clamp delta for moving clips so none start before frame 0. */
export function clampMoveDelta(tl: Timeline, ids: Iterable<string>, delta: number): number {
  let minStart = Infinity;
  const set = new Set(ids);
  for (const t of tl.tracks) for (const c of t.clips) if (set.has(c.id)) minStart = Math.min(minStart, c.start);
  return minStart === Infinity ? 0 : Math.max(delta, -minStart);
}

export type Edge = "start" | "end";

interface Bounds {
  min: number;
  max: number;
}

/** Allowed delta range for trimming one clip edge. `neighbors` stops at adjacent clips. */
function trimBounds(track: Track, c: Clip, edge: Edge, ctx: OpCtx, neighbors: boolean, ripple: boolean): Bounds {
  const b: Bounds = { min: -Infinity, max: Infinity };
  const src = usesSource(c) ? ctx.sourceFrames?.(c) : undefined;
  if (edge === "start") {
    b.max = c.duration - 1;
    if (!ripple) b.min = -c.start;
    if (usesSource(c)) b.min = Math.max(b.min, -Math.floor(c.in / c.speed));
    if (neighbors && !ripple) {
      const prevEnd = Math.max(0, ...track.clips.filter((o) => o.id !== c.id && clipEnd(o) <= c.start).map(clipEnd));
      b.min = Math.max(b.min, prevEnd - c.start);
    }
  } else {
    b.min = 1 - c.duration;
    if (src !== undefined) b.max = Math.floor((src - c.in) / c.speed) - c.duration;
    if (neighbors && !ripple) {
      const nexts = track.clips.filter((o) => o.id !== c.id && o.start >= clipEnd(c)).map((o) => o.start);
      if (nexts.length) b.max = Math.min(b.max, Math.min(...nexts) - clipEnd(c));
    }
  }
  // a zero-length trim is always allowed
  if (b.max < 0) b.max = 0;
  if (b.min > 0) b.min = 0;
  return b;
}

function applyTrim(c: Clip, edge: Edge, d: number, ripple: boolean): void {
  if (edge === "end") {
    c.duration += d;
    return;
  }
  // head trim: `in` moves with the edge; keyframes stay put on the timeline
  if (!c.freeze) c.in += srcFrames(c, d);
  c.duration -= d;
  shiftKeyframes(c, -d);
  if (!ripple) c.start += d;
}

/** Clamp a trim delta for a set of clips (they trim together, e.g. linked A/V). */
export function clampTrim(tl: Timeline, ids: string[], edge: Edge, delta: number, ctx: OpCtx = {}, ripple = false): number {
  let lo = -Infinity;
  let hi = Infinity;
  for (const id of ids) {
    const hit = findClip(tl, id);
    if (!hit) continue;
    const b = trimBounds(hit.track, hit.clip, edge, ctx, true, ripple);
    lo = Math.max(lo, b.min);
    hi = Math.min(hi, b.max);
  }
  if (lo > hi) return 0;
  return Math.min(hi, Math.max(lo, delta));
}

/** Normal trim of one edge of each clip by `delta` frames (clamped; never overlaps neighbours). */
export function trim(tl: Timeline, ids: string[], edge: Edge, delta: number, ctx: OpCtx = {}): Timeline {
  const d = clampTrim(tl, ids, edge, delta, ctx, false);
  if (!d) return tl;
  const out = cloneTimeline(tl);
  for (const id of ids) {
    const hit = findClip(out, id);
    if (hit) applyTrim(hit.clip, edge, d, false);
  }
  for (const t of out.tracks) sortTrack(t);
  return out;
}

/**
 * Ripple trim: the edge moves and everything after the clip on its track shifts
 * by the same amount (head trims keep the clip's start; later clips move left).
 */
export function rippleTrim(tl: Timeline, ids: string[], edge: Edge, delta: number, ctx: OpCtx = {}): Timeline {
  const d = clampTrim(tl, ids, edge, delta, ctx, true);
  if (!d) return tl;
  const out = cloneTimeline(tl);
  const set = new Set(ids);
  for (const t of out.tracks) {
    const mine = t.clips.filter((c) => set.has(c.id));
    if (!mine.length) continue;
    const pivot = Math.min(...mine.map(clipEnd)); // later clips start at/after the original end
    const shift = edge === "end" ? d : -d;
    for (const c of t.clips) {
      if (set.has(c.id)) applyTrim(c, edge, d, true);
      else if (c.start >= pivot) shiftClip(c, shift);
    }
    sortTrack(t);
  }
  return out;
}

/** Q / W: ripple-trim the clip under the playhead's head (Q) or tail (W) to the playhead. */
export function rippleTrimToPlayhead(tl: Timeline, f: number, trackIds: string[], side: "head" | "tail", ctx: OpCtx = {}): Timeline {
  let out = tl;
  for (const tid of trackIds) {
    const t = out.tracks.find((x) => x.id === tid);
    if (!t || t.locked) continue;
    const c = t.clips.find((x) => x.start < f && f < clipEnd(x));
    if (!c) continue;
    out = side === "head" ? rippleTrim(out, [c.id], "start", f - c.start, ctx) : rippleTrim(out, [c.id], "end", f - clipEnd(c), ctx);
  }
  return out;
}

/** Roll edit: move the cut between adjacent clips (left end + right start) together. */
export function roll(tl: Timeline, pairs: Array<[string, string]>, delta: number, ctx: OpCtx = {}): Timeline {
  let lo = -Infinity;
  let hi = Infinity;
  for (const [l, r] of pairs) {
    const L = findClip(tl, l);
    const R = findClip(tl, r);
    if (!L || !R) return tl;
    const bl = trimBounds(L.track, L.clip, "end", ctx, false, false);
    const br = trimBounds(R.track, R.clip, "start", ctx, false, false);
    lo = Math.max(lo, bl.min, br.min);
    hi = Math.min(hi, bl.max, br.max);
  }
  if (lo > hi) return tl;
  const d = Math.min(hi, Math.max(lo, delta));
  if (!d) return tl;
  const out = cloneTimeline(tl);
  for (const [l, r] of pairs) {
    const L = findClip(out, l)!;
    const R = findClip(out, r)!;
    applyTrim(L.clip, "end", d, false);
    applyTrim(R.clip, "start", d, false);
  }
  return out;
}

/** Find the clip that ends exactly where `id` starts (or starts where it ends) on the same track. */
export function adjacent(tl: Timeline, id: string, edge: Edge): Clip | null {
  const hit = findClip(tl, id);
  if (!hit) return null;
  const { clip, track } = hit;
  if (edge === "start") return track.clips.find((o) => o.id !== id && clipEnd(o) === clip.start) ?? null;
  return track.clips.find((o) => o.id !== id && o.start === clipEnd(clip)) ?? null;
}

// ── snapping ─────────────────────────────────────────────────────────

/** Snap `f` to the nearest candidate within `threshold` frames. */
export function snap(f: number, candidates: number[], threshold: number): { f: number; target: number | null } {
  let best: number | null = null;
  let bestD = threshold + 1e-9;
  for (const c of candidates) {
    const d = Math.abs(c - f);
    if (d <= bestD) {
      bestD = d;
      best = c;
    }
  }
  return best === null ? { f, target: null } : { f: best, target: best };
}

/** Adjust a move delta so that any of `edges` (+delta) lands on a candidate within threshold. */
export function snapDelta(edges: number[], delta: number, candidates: number[], threshold: number): { delta: number; target: number | null } {
  let best: { delta: number; target: number } | null = null;
  let bestD = threshold + 1e-9;
  for (const e of edges) {
    for (const c of candidates) {
      const d = Math.abs(e + delta - c);
      if (d < bestD) {
        bestD = d;
        best = { delta: c - e, target: c };
      }
    }
  }
  return best ?? { delta, target: null };
}

/** All clip edges (optionally excluding some clips) + markers + extra points. */
export function snapCandidates(tl: Timeline, exclude: Set<string>, extra: number[] = []): number[] {
  const s = new Set<number>(extra);
  s.add(0);
  for (const t of tl.tracks) {
    for (const c of t.clips) {
      if (exclude.has(c.id)) continue;
      s.add(c.start);
      s.add(clipEnd(c));
    }
  }
  for (const m of tl.markers) s.add(m.f);
  return [...s];
}

// ── navigation / queries ─────────────────────────────────────────────

export function editPoints(tl: Timeline, trackIds?: string[]): number[] {
  const s = new Set<number>([0]);
  for (const t of tl.tracks) {
    if (trackIds && !trackIds.includes(t.id)) continue;
    for (const c of t.clips) {
      s.add(c.start);
      s.add(clipEnd(c));
    }
  }
  return [...s].sort((a, b) => a - b);
}

export function nextEdit(tl: Timeline, f: number, dir: 1 | -1, trackIds?: string[]): number {
  const pts = editPoints(tl, trackIds);
  if (dir > 0) return pts.find((p) => p > f) ?? f;
  for (let i = pts.length - 1; i >= 0; i--) if (pts[i]! < f) return pts[i]!;
  return f;
}

/** Ids plus every clip sharing a `linked` group with them. */
export function expandLinked(tl: Timeline, ids: Iterable<string>): string[] {
  const set = new Set(ids);
  const links = new Set<string>();
  for (const t of tl.tracks) for (const c of t.clips) if (set.has(c.id) && c.linked) links.add(c.linked);
  if (!links.size) return [...set];
  for (const t of tl.tracks) for (const c of t.clips) if (c.linked && links.has(c.linked)) set.add(c.id);
  return [...set];
}

export function clipsAt(tl: Timeline, f: number, trackIds?: string[]): Clip[] {
  const out: Clip[] = [];
  for (const t of tl.tracks) {
    if (trackIds && !trackIds.includes(t.id)) continue;
    for (const c of t.clips) if (c.start <= f && f < clipEnd(c)) out.push(c);
  }
  return out;
}

// ── clip / track / marker edits ──────────────────────────────────────

export function updateClip(tl: Timeline, id: string, fn: (c: Clip) => void): Timeline {
  const out = cloneTimeline(tl);
  const hit = findClip(out, id);
  if (!hit) return tl;
  fn(hit.clip);
  hit.clip.start = Math.max(0, Math.round(hit.clip.start));
  hit.clip.duration = Math.max(1, Math.round(hit.clip.duration));
  hit.clip.in = Math.max(0, Math.round(hit.clip.in));
  sortTrack(hit.track);
  return out;
}

export function updateClips(tl: Timeline, ids: Iterable<string>, fn: (c: Clip) => void): Timeline {
  const set = new Set(ids);
  const out = cloneTimeline(tl);
  for (const t of out.tracks) for (const c of t.clips) if (set.has(c.id)) fn(c);
  return out;
}

/** Set (or replace) a keyframe at clip-relative frame `fRel`. */
export function setKeyframe(tl: Timeline, id: string, name: string, fRel: number, v: number): Timeline {
  return updateClip(tl, id, (c) => {
    const list = (c.keyframes[name] ?? []).filter((k) => k.f !== fRel);
    const prevEase = (c.keyframes[name] ?? []).find((k) => k.f === fRel)?.ease ?? "ease";
    list.push({ f: Math.round(fRel), v, ease: prevEase });
    list.sort((a, b) => a.f - b.f);
    c.keyframes = { ...c.keyframes, [name]: list };
  });
}

export function removeKeyframe(tl: Timeline, id: string, name: string, fRel: number): Timeline {
  return updateClip(tl, id, (c) => {
    const list = (c.keyframes[name] ?? []).filter((k) => k.f !== fRel);
    const kf = { ...c.keyframes };
    if (list.length) kf[name] = list;
    else delete kf[name];
    c.keyframes = kf;
  });
}

export function updateTrack(tl: Timeline, id: string, fn: (t: Track) => void): Timeline {
  const out = cloneTimeline(tl);
  const t = out.tracks.find((x) => x.id === id);
  if (!t) return tl;
  fn(t);
  return out;
}

/** Add a track: video tracks go on top (end of list), audio tracks at the bottom. */
export function addTrack(tl: Timeline, kind: TrackKind): { tl: Timeline; id: string } {
  const out = cloneTimeline(tl);
  const n = out.tracks.filter((t) => t.kind === kind).length + 1;
  const tr = makeTrack(kind, `${kind === "video" ? "V" : "A"}${n}`);
  if (kind === "video") {
    const lastVideo = out.tracks.map((t) => t.kind).lastIndexOf("video");
    out.tracks.splice(lastVideo + 1, 0, tr);
  } else out.tracks.push(tr);
  return { tl: out, id: tr.id };
}

export function removeTrack(tl: Timeline, id: string): Timeline {
  const out = cloneTimeline(tl);
  out.tracks = out.tracks.filter((t) => t.id !== id);
  return out;
}

export function addMarker(tl: Timeline, m: Partial<Marker> & { f: number }): Timeline {
  const out = cloneTimeline(tl);
  out.markers.push({ f: Math.max(0, Math.round(m.f)), label: m.label ?? "", color: m.color ?? "#FF6B35", duration: m.duration ?? 0 });
  out.markers.sort((a, b) => a.f - b.f);
  return out;
}

export function moveMarker(tl: Timeline, index: number, f: number): Timeline {
  const out = cloneTimeline(tl);
  const m = out.markers[index];
  if (!m) return tl;
  m.f = Math.max(0, Math.round(f));
  return out;
}

export function removeMarker(tl: Timeline, index: number): Timeline {
  const out = cloneTimeline(tl);
  out.markers.splice(index, 1);
  return out;
}

/**
 * Paste copied clips at frame `at`, keeping their relative timing and tracks
 * (falling back to the first track of the same kind). Overwrite edit.
 */
export function paste(tl: Timeline, clips: Array<{ clip: Clip; trackId: string; kind: TrackKind }>, at: number, fallback: Partial<Record<TrackKind, string>> = {}): { tl: Timeline; ids: string[] } {
  if (!clips.length) return { tl, ids: [] };
  const minStart = Math.min(...clips.map((c) => c.clip.start));
  const relink = linkMapper();
  const ids: string[] = [];
  const placements = clips.map(({ clip, trackId, kind }) => {
    const c = structuredClone(clip);
    c.id = newId("c");
    ids.push(c.id);
    c.linked = relink(clip.linked);
    shiftClip(c, at + (clip.start - minStart) - clip.start);
    const exists = tl.tracks.find((t) => t.id === trackId && t.kind === kind && !t.locked);
    const tid = exists ? trackId : fallback[kind] ?? tl.tracks.find((t) => t.kind === kind && !t.locked)?.id ?? "";
    return { trackId: tid, clip: c };
  });
  return { tl: overwrite(tl, placements.filter((p) => p.trackId)), ids };
}

/** First video track where [start, end) is free (bottom→top), or null. */
export function freeTrack(tl: Timeline, kind: TrackKind, start: number, end: number, exclude: Set<string> = new Set()): string | null {
  for (const t of tl.tracks) {
    if (t.kind !== kind || t.locked || exclude.has(t.id)) continue;
    if (t.clips.every((c) => clipEnd(c) <= start || c.start >= end)) return t.id;
  }
  return null;
}
