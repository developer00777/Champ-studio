// Editing commands shared by keyboard shortcuts, toolbar buttons and menus.

import { media } from "./media";
import { clipEnd, defaultAdjust, findClip, makeClip, timelineDuration, type Clip, type Timeline, type TrackKind } from "./model";
import * as ops from "./ops";
import { store } from "./store";
import { toast } from "./ui";

export function sourceCtx(): ops.OpCtx {
  return {
    sourceFrames: (c: Clip) => {
      if (!c.asset_id) return undefined;
      const d = media.duration(c.asset_id);
      return d ? Math.floor(d * store.fps + 1e-6) : undefined;
    },
  };
}

function guard(): boolean {
  if (store.readOnly) {
    toast(store.readOnlyReason || "This sequence is read-only", "warn");
    return false;
  }
  return true;
}

export function editableTrackIds(kind?: TrackKind): string[] {
  return store.doc.tracks.filter((t) => !t.locked && (!kind || t.kind === kind)).map((t) => t.id);
}

/** Targeted, unlocked tracks (falls back to all unlocked tracks). */
export function targetTrackIds(): string[] {
  const t = store.doc.tracks.filter((x) => store.targets.has(x.id) && !x.locked).map((x) => x.id);
  return t.length ? t : editableTrackIds();
}

/** Selected clips (+ linked partners unless linked selection is off), excluding locked tracks. */
export function selectedIds(expand = store.linkedSelection): string[] {
  const ids = expand ? ops.expandLinked(store.doc, store.selection) : [...store.selection];
  return ids.filter((id) => {
    const hit = findClip(store.doc, id);
    return hit && !hit.track.locked;
  });
}

export function splitAtPlayhead(): void {
  if (!guard()) return;
  const f = store.playhead;
  const sel = selectedIds().filter((id) => {
    const c = findClip(store.doc, id)?.clip;
    return c && c.start < f && f < clipEnd(c);
  });
  const label = "Add edit";
  if (sel.length) store.apply(label, (tl) => ops.splitAt(tl, f, { clipIds: ops.expandLinked(tl, sel) }));
  else store.apply(label, (tl) => ops.splitAt(tl, f, { trackIds: targetTrackIds() }));
}

export function razorAt(clipId: string, f: number, alone: boolean): void {
  if (!guard()) return;
  const ids = alone || !store.linkedSelection ? [clipId] : ops.expandLinked(store.doc, [clipId]);
  store.apply("Razor", (tl) => ops.splitAt(tl, f, { clipIds: ids }));
}

export function deleteSelection(ripple: boolean): void {
  if (!guard()) return;
  const ids = selectedIds();
  if (!ids.length) return;
  store.apply(ripple ? "Ripple delete" : "Delete", (tl) => (ripple ? ops.rippleDelete(tl, ids) : ops.lift(tl, ids)));
}

export function rippleTrimPlayhead(side: "head" | "tail"): void {
  if (!guard()) return;
  const f = store.playhead;
  const tracks = targetTrackIds();
  const before = store.doc;
  const out = ops.rippleTrimToPlayhead(before, f, tracks, side, sourceCtx());
  if (out === before) return;
  store.apply(side === "head" ? "Ripple trim previous edit to playhead" : "Ripple trim next edit to playhead", out);
  if (side === "head") {
    // the playhead lands on the new edit point (where the trimmed head now starts)
    const c = ops.clipsAt(before, f, tracks)[0];
    if (c) store.setPlayhead(c.start);
  }
}

export function nudgeSelection(delta: number): void {
  if (!guard()) return;
  const ids = selectedIds();
  if (!ids.length) return;
  const d = ops.clampMoveDelta(store.doc, ids, delta);
  if (!d) return;
  store.apply("Nudge", (tl) => ops.moveClips(tl, ids, d), { coalesce: "nudge" });
}

// ── clipboard ──

let clipboard: Array<{ clip: Clip; trackId: string; kind: TrackKind }> = [];

export function copySelection(cut = false): void {
  const ids = selectedIds();
  if (!ids.length) return;
  clipboard = [];
  for (const t of store.doc.tracks) for (const c of t.clips) if (ids.includes(c.id)) clipboard.push({ clip: structuredClone(c), trackId: t.id, kind: t.kind });
  if (cut) deleteSelection(false);
  else toast(`Copied ${clipboard.length} clip${clipboard.length === 1 ? "" : "s"}`, "info", 1600);
}

export function pasteAtPlayhead(): void {
  if (!guard() || !clipboard.length) return;
  const fallback: Partial<Record<TrackKind, string>> = {};
  for (const id of targetTrackIds()) {
    const t = store.doc.tracks.find((x) => x.id === id)!;
    fallback[t.kind] ??= t.id;
  }
  let ids: string[] = [];
  store.apply("Paste", (tl) => {
    const r = ops.paste(tl, clipboard, store.playhead, fallback);
    ids = r.ids;
    return r.tl;
  });
  store.select(ids);
  const len = Math.max(...clipboard.map((c) => c.clip.start + c.clip.duration)) - Math.min(...clipboard.map((c) => c.clip.start));
  store.setPlayhead(store.playhead + len);
}

// ── generated clips ──

function brandStyle(kind: "title" | "lower_third"): Partial<Clip["style"]> {
  const b = store.brand;
  const s = (b?.styles?.[kind] ?? b?.styles?.title ?? {}) as Partial<Clip["style"]>;
  return s;
}

/** Insert a clip on the first free video track at the playhead (creating a track if needed). */
export function placeOverlay(clip: Clip, preferTrack?: string): void {
  if (!guard()) return;
  store.apply(`Add ${clip.type === "text" ? "title" : clip.type}`, (tl) => {
    let out: Timeline = tl;
    let tid: string | null = null;
    const pref = preferTrack ? tl.tracks.find((t) => t.id === preferTrack) : undefined;
    if (pref && !pref.locked && pref.clips.every((c) => clipEnd(c) <= clip.start || c.start >= clipEnd(clip))) tid = pref.id;
    tid ??= highestFreeVideoTrack(tl, clip.start, clipEnd(clip));
    if (!tid) {
      const r = ops.addTrack(tl, "video");
      out = r.tl;
      tid = r.id;
    }
    return ops.overwrite(out, [{ trackId: tid, clip }]);
  });
  store.select([clip.id]);
}

/** Overlays go above existing picture: prefer the lowest free track above the topmost used one. */
function highestFreeVideoTrack(tl: Timeline, s: number, e: number): string | null {
  const vids = tl.tracks.filter((t) => t.kind === "video");
  let topUsed = -1;
  vids.forEach((t, i) => {
    if (t.clips.some((c) => c.start < e && clipEnd(c) > s)) topUsed = i;
  });
  for (let i = topUsed + 1; i < vids.length; i++) if (!vids[i]!.locked) return vids[i]!.id;
  return null;
}

export function addTitle(): void {
  const fps = store.fps;
  const clip = makeClip({
    type: "text",
    name: "Title",
    text: "Your title here",
    start: store.playhead,
    duration: fps * 4,
    box: [0.1, 0.38, 0.8, 0.24],
    anim_in: { type: "fade", duration: 12 },
    anim_out: { type: "fade", duration: 12 },
  });
  Object.assign(clip.style, brandStyle("title"));
  placeOverlay(clip);
}

export function addColorMatte(): void {
  const b = store.brand;
  const clip = makeClip({ type: "color", name: "Color matte", color: b?.colors?.primary ?? "#FF6B35", start: store.playhead, duration: store.fps * 5 });
  placeOverlay(clip);
}

export function addShape(): void {
  const b = store.brand;
  const clip = makeClip({ type: "shape", name: "Shape", start: store.playhead, duration: store.fps * 5, box: [0.06, 0.72, 0.42, 0.14] });
  clip.shape.fill = b?.colors?.primary ?? "#FF6B35";
  clip.shape.radius = 0.2;
  placeOverlay(clip);
}

export function addAdjustmentLayer(): void {
  const clip = makeClip({ type: "adjustment", name: "Adjustment layer", start: store.playhead, duration: store.fps * 5, adjust: defaultAdjust() });
  placeOverlay(clip);
}

export function addTrack(kind: TrackKind): void {
  if (!guard()) return;
  let id = "";
  store.apply(`Add ${kind} track`, (tl) => {
    const r = ops.addTrack(tl, kind);
    id = r.id;
    return r.tl;
  });
  if (id) toast(`Added ${kind} track`, "success", 1500);
}

export function addMarker(): void {
  if (!guard()) return;
  store.apply("Add marker", (tl) => ops.addMarker(tl, { f: store.playhead }));
}

// ── marks / navigation ──

export function markIn(): void {
  store.setMarks(store.playhead, store.markOut !== null && store.markOut <= store.playhead ? null : store.markOut);
}
export function markOut(): void {
  store.setMarks(store.markIn !== null && store.markIn >= store.playhead ? null : store.markIn, store.playhead);
}

export function jumpEdit(dir: 1 | -1): void {
  const tracks = store.targets.size ? [...store.targets] : undefined;
  store.setPlayhead(ops.nextEdit(store.doc, store.playhead, dir, tracks));
}

export function goEnd(): void {
  store.setPlayhead(Math.max(0, timelineDuration(store.doc) - 1));
}

export function toggleEnabled(): void {
  if (!guard()) return;
  const ids = selectedIds();
  if (!ids.length) return;
  const anyOn = ids.some((id) => findClip(store.doc, id)?.clip.enabled);
  store.apply(anyOn ? "Disable clips" : "Enable clips", (tl) => ops.updateClips(tl, ids, (c) => (c.enabled = !anyOn)));
}

export function linkSelection(): void {
  if (!guard()) return;
  const ids = [...store.selection];
  if (!ids.length) return;
  const linkedAlready = ids.every((id) => findClip(store.doc, id)?.clip.linked);
  if (linkedAlready) {
    store.apply("Unlink", (tl) => ops.updateClips(tl, ops.expandLinked(tl, ids), (c) => (c.linked = null)));
    toast("Unlinked", "info", 1400);
  } else if (ids.length > 1) {
    const lk = `lk_${Date.now().toString(36)}`;
    store.apply("Link", (tl) => ops.updateClips(tl, ids, (c) => (c.linked = lk)));
    toast("Linked", "info", 1400);
  }
}
