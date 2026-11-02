import { describe, expect, it } from "vitest";
import { clipEnd, evalKeyframes, findClip, makeClip, makeTrack, normalizeTimeline, parseTimecode, prop, timecode, type Clip, type Timeline } from "../src/model";
import {
  addMarker,
  addTrack,
  clampMoveDelta,
  editPoints,
  expandLinked,
  insert,
  lift,
  moveClips,
  nextEdit,
  overwrite,
  paste,
  rippleDelete,
  rippleDeleteRange,
  rippleTrim,
  rippleTrimToPlayhead,
  roll,
  setKeyframe,
  snap,
  snapDelta,
  splitAt,
  splitClipObject,
  trim,
} from "../src/ops";

function fixture(): Timeline {
  const v1 = makeTrack("video", "V1", "v1");
  const v2 = makeTrack("video", "V2", "v2");
  const a1 = makeTrack("audio", "A1", "a1");
  v1.clips = [
    makeClip({ id: "A", type: "video", start: 0, duration: 60, in: 30, asset_id: "x", linked: "L1" }),
    makeClip({ id: "B", type: "video", start: 60, duration: 40, in: 0, asset_id: "y" }),
    makeClip({ id: "C", type: "video", start: 120, duration: 30, in: 10, asset_id: "z" }),
  ];
  a1.clips = [makeClip({ id: "Aa", type: "audio", start: 0, duration: 60, in: 30, asset_id: "x", linked: "L1" })];
  v2.clips = [makeClip({ id: "T", type: "text", start: 30, duration: 50, text: "Hi" })];
  return normalizeTimeline({ fps: 30, width: 1920, height: 1080, tracks: [v1, v2, a1] });
}

const clip = (tl: Timeline, id: string): Clip => findClip(tl, id)!.clip;
const ids = (tl: Timeline, track: string) => tl.tracks.find((t) => t.id === track)!.clips.map((c) => c.id);
const allIntegers = (tl: Timeline) =>
  tl.tracks.every((t) => t.clips.every((c) => Number.isInteger(c.start) && Number.isInteger(c.duration) && Number.isInteger(c.in)));

describe("model helpers", () => {
  it("formats and parses timecode", () => {
    expect(timecode(0, 30)).toBe("00:00:00:00");
    expect(timecode(30 * 3661 + 7, 30)).toBe("01:01:01:07");
    expect(parseTimecode("01:01:01:07", 30)).toBe(30 * 3661 + 7);
    expect(parseTimecode("2s", 25)).toBe(50);
    expect(parseTimecode("10:05", 30)).toBe(305);
  });

  it("evaluates keyframes like the engine", () => {
    const kfs = [
      { f: 0, v: 0, ease: "linear" as const },
      { f: 10, v: 1, ease: "ease" as const },
      { f: 20, v: 0, ease: "hold" as const },
    ];
    expect(evalKeyframes(kfs, -5, 9)).toBe(0);
    expect(evalKeyframes(kfs, 5, 9)).toBeCloseTo(0.5);
    expect(evalKeyframes(kfs, 15, 9)).toBeCloseTo(0.5); // smoothstep midpoint
    expect(evalKeyframes(kfs, 25, 9)).toBe(0);
    expect(evalKeyframes([], 3, 9)).toBe(9);
  });
});

describe("split", () => {
  it("splits a clip with exact source offsets", () => {
    const tl = splitAt(fixture(), 20, { trackIds: ["v1"] });
    const [l, r] = tl.tracks[0]!.clips;
    expect(l!.id).toBe("A");
    expect(l!.duration).toBe(20);
    expect(r!.start).toBe(20);
    expect(r!.duration).toBe(40);
    expect(r!.in).toBe(50);
    expect(allIntegers(tl)).toBe(true);
  });

  it("rounds source offsets for speed-changed clips", () => {
    const c = makeClip({ id: "S", type: "video", start: 0, duration: 10, in: 3, speed: 1.5 });
    const [, r] = splitClipObject(c, 3);
    expect(r.in).toBe(3 + Math.round(3 * 1.5));
    expect(Number.isInteger(r.in)).toBe(true);
  });

  it("keeps keyframe curves identical across the cut", () => {
    let tl = fixture();
    tl = setKeyframe(tl, "A", "opacity", 0, 0);
    tl = setKeyframe(tl, "A", "opacity", 40, 1);
    const before = prop(clip(tl, "A"), "opacity", 25, 1);
    tl = splitAt(tl, 20, { trackIds: ["v1"] });
    const right = tl.tracks[0]!.clips[1]!;
    expect(prop(right, "opacity", 5, 1)).toBeCloseTo(before);
  });

  it("gives linked right halves a shared new link id and drops in/out anims", () => {
    let tl = fixture();
    tl = structuredClone(tl);
    clip(tl, "A").anim_in = { type: "fade", duration: 10 };
    clip(tl, "A").anim_out = { type: "fade", duration: 10 };
    tl = splitAt(tl, 30);
    const vr = tl.tracks[0]!.clips[1]!;
    const ar = tl.tracks[2]!.clips[1]!;
    expect(vr.linked).toBeTruthy();
    expect(vr.linked).toBe(ar.linked);
    expect(vr.linked).not.toBe("L1");
    expect(clip(tl, "A").anim_out).toBeNull();
    expect(vr.anim_in).toBeNull();
    expect(vr.anim_out).toEqual({ type: "fade", duration: 10 });
  });

  it("does not split at clip boundaries or on locked tracks", () => {
    const tl0 = fixture();
    tl0.tracks[0]!.locked = true;
    const tl = splitAt(tl0, 30);
    expect(ids(tl, "v1")).toEqual(["A", "B", "C"]);
    expect(splitAt(fixture(), 60, { trackIds: ["v1"] }).tracks[0]!.clips).toHaveLength(3);
  });

  it("does not mutate the input", () => {
    const tl = fixture();
    const snapshot = JSON.stringify(tl);
    splitAt(tl, 20);
    rippleDelete(tl, ["B"]);
    moveClips(tl, ["A"], 5);
    expect(JSON.stringify(tl)).toBe(snapshot);
  });
});

describe("trim", () => {
  it("trims the head moving `in` and keyframes stay in timeline position", () => {
    let tl = setKeyframe(fixture(), "B", "x", 10, 0.5);
    tl = trim(tl, ["B"], "start", 5);
    const b = clip(tl, "B");
    expect(b.start).toBe(65);
    expect(b.duration).toBe(35);
    expect(b.in).toBe(5);
    expect(b.keyframes.x![0]!.f).toBe(5);
  });

  it("clamps head trims to available source and neighbours", () => {
    const tl = trim(fixture(), ["B"], "start", -50);
    expect(clip(tl, "B").start).toBe(60); // B.in = 0: no handle
    const tl2 = trim(fixture(), ["C"], "start", -50);
    expect(clip(tl2, "C").start).toBe(110); // C.in = 10
    expect(clip(tl2, "C").in).toBe(0);
  });

  it("clamps tail trims to the next clip and to source length", () => {
    const tl = trim(fixture(), ["B"], "end", 100);
    expect(clipEnd(clip(tl, "B"))).toBe(120);
    const tl2 = trim(fixture(), ["C"], "end", 100, { sourceFrames: () => 60 });
    expect(clip(tl2, "C").duration).toBe(50); // in 10 + 50 = 60 source frames
  });

  it("never trims below one frame", () => {
    const tl = trim(fixture(), ["B"], "end", -500);
    expect(clip(tl, "B").duration).toBe(1);
  });

  it("trims linked clips together", () => {
    const tl = trim(fixture(), ["A", "Aa"], "end", -10);
    expect(clip(tl, "A").duration).toBe(50);
    expect(clip(tl, "Aa").duration).toBe(50);
  });
});

describe("ripple", () => {
  it("ripple deletes and closes the gap on that track only", () => {
    const tl = rippleDelete(fixture(), ["B"]);
    expect(ids(tl, "v1")).toEqual(["A", "C"]);
    expect(clip(tl, "C").start).toBe(80);
    expect(clip(tl, "T").start).toBe(30);
  });

  it("ripple delete only closes real gaps", () => {
    let tl = fixture();
    tl = overwrite(tl, [{ trackId: "v2", clip: makeClip({ id: "U", type: "text", start: 90, duration: 20 }) }]);
    tl = rippleDelete(tl, ["T"]); // T: 30..80 on v2; U after it moves left by 50
    expect(clip(tl, "U").start).toBe(40);
  });

  it("ripple trims the tail and shifts later clips", () => {
    const tl = rippleTrim(fixture(), ["B"], "end", -10);
    expect(clip(tl, "B").duration).toBe(30);
    expect(clip(tl, "C").start).toBe(110);
  });

  it("ripple trims the head keeping the clip start", () => {
    const tl = rippleTrim(fixture(), ["B"], "start", 10);
    const b = clip(tl, "B");
    expect(b.start).toBe(60);
    expect(b.in).toBe(10);
    expect(b.duration).toBe(30);
    expect(clip(tl, "C").start).toBe(110);
  });

  it("Q/W ripple-trims to the playhead", () => {
    const q = rippleTrimToPlayhead(fixture(), 70, ["v1"], "head");
    expect(clip(q, "B").duration).toBe(30);
    expect(clip(q, "B").in).toBe(10);
    expect(clip(q, "C").start).toBe(110);
    const w = rippleTrimToPlayhead(fixture(), 70, ["v1"], "tail");
    expect(clip(w, "B").duration).toBe(10);
    expect(clip(w, "C").start).toBe(90);
  });

  it("ripple deletes a range", () => {
    const tl = rippleDeleteRange(fixture(), 10, 20, ["v1"]);
    const v1 = tl.tracks[0]!.clips;
    expect(v1[0]!.duration).toBe(10);
    expect(v1[1]!.start).toBe(10);
    expect(v1[1]!.in).toBe(50);
    expect(clip(tl, "B").start).toBe(50);
  });
});

describe("move / insert / overwrite", () => {
  it("moves clips and overwrites what they land on", () => {
    const tl = moveClips(fixture(), ["B"], 50); // B → 110..150 overwrites C 120..150
    expect(ids(tl, "v1")).toEqual(["A", "B"]);
    expect(clip(tl, "B").start).toBe(110);
  });

  it("splits a clip that the moved clip lands inside", () => {
    const tl = moveClips(fixture(), ["T"], -20, { v2: "v1" }); // T 10..60 onto v1 over A 0..60
    const v1 = tl.tracks[0]!.clips;
    expect(v1.map((c) => [c.start, c.duration])).toEqual([
      [0, 10],
      [10, 50],
      [60, 40],
      [120, 30],
    ]);
    expect(ids(tl, "v2")).toEqual([]);
  });

  it("clamps moves at frame 0", () => {
    expect(clampMoveDelta(fixture(), ["B"], -100)).toBe(-60);
    const tl = moveClips(fixture(), ["T"], -100);
    expect(clip(tl, "T").start).toBe(0);
  });

  it("insert pushes later clips right", () => {
    const n = makeClip({ id: "N", type: "video", start: 0, duration: 15 });
    const tl = insert(fixture(), [{ trackId: "v1", clip: n }], 30);
    expect(tl.tracks[0]!.clips.map((c) => [c.id.length > 1 ? "r" : c.id, c.start, c.duration])).toEqual([
      ["A", 0, 30],
      ["N", 30, 15],
      ["r", 45, 30],
      ["B", 75, 40],
      ["C", 135, 30],
    ]);
    expect(clip(tl, "T").start).toBe(30); // other tracks untouched
  });

  it("overwrite trims clips underneath", () => {
    const n = makeClip({ id: "N", type: "video", start: 50, duration: 20 });
    const tl = overwrite(fixture(), [{ trackId: "v1", clip: n }]);
    expect(clip(tl, "A").duration).toBe(50);
    expect(clip(tl, "B").start).toBe(70);
    expect(clip(tl, "B").in).toBe(10);
    expect(clip(tl, "B").duration).toBe(30);
  });

  it("moves captions words with their clip", () => {
    const tl0 = fixture();
    const cap = makeClip({ id: "K", type: "captions", start: 200, duration: 30, captions: { words: [{ w: "hi", s: 200, e: 210 }], mode: "karaoke", max_chars: 32, max_lines: 2 } });
    const tl1 = overwrite(tl0, [{ trackId: "v2", clip: cap }]);
    const tl = moveClips(tl1, ["K"], 10);
    expect(clip(tl, "K").captions!.words[0]).toEqual({ w: "hi", s: 210, e: 220 });
  });
});

describe("roll / lift / misc", () => {
  it("rolls an edit between adjacent clips", () => {
    const tl = roll(fixture(), [["A", "B"]], 5);
    expect(clip(tl, "A").duration).toBe(65);
    expect(clip(tl, "B").start).toBe(65);
    expect(clip(tl, "B").in).toBe(5);
    expect(clip(tl, "B").duration).toBe(35);
  });

  it("clamps rolls to the right clip's handles", () => {
    const tl = roll(fixture(), [["A", "B"]], -10); // B.in = 0 → cannot extend B's head
    expect(clip(tl, "A").duration).toBe(60);
  });

  it("lifts leaving a gap", () => {
    const tl = lift(fixture(), ["B"]);
    expect(clip(tl, "C").start).toBe(120);
  });

  it("expands linked selection", () => {
    expect(expandLinked(fixture(), ["A"]).sort()).toEqual(["A", "Aa"]);
    expect(expandLinked(fixture(), ["B"])).toEqual(["B"]);
  });

  it("snaps to nearby candidates", () => {
    expect(snap(58, [0, 60, 120], 3)).toEqual({ f: 60, target: 60 });
    expect(snap(50, [0, 60], 3)).toEqual({ f: 50, target: null });
    expect(snapDelta([60, 100], 18, [120], 3)).toEqual({ delta: 20, target: 120 });
  });

  it("navigates edit points", () => {
    const tl = fixture();
    expect(editPoints(tl, ["v1"])).toEqual([0, 60, 100, 120, 150]);
    expect(nextEdit(tl, 60, 1, ["v1"])).toBe(100);
    expect(nextEdit(tl, 60, -1, ["v1"])).toBe(0);
  });

  it("pastes clips with new ids at the playhead", () => {
    const tl0 = fixture();
    const src = [{ clip: clip(tl0, "B"), trackId: "v1", kind: "video" as const }];
    const { tl, ids: newIds } = paste(tl0, src, 200);
    expect(newIds).toHaveLength(1);
    expect(clip(tl, newIds[0]!).start).toBe(200);
  });

  it("adds tracks and markers", () => {
    const { tl, id } = addTrack(fixture(), "video");
    expect(tl.tracks.map((t) => t.id).indexOf(id)).toBe(2); // after V2, before audio
    const m = addMarker(tl, { f: 12.4 });
    expect(m.markers[0]!.f).toBe(12);
  });
});
