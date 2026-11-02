import { describe, expect, it } from "vitest";
import { aspectOf, makeClip, normalizeTimeline } from "../src/model";
import { captionLines } from "../src/text";

describe("captions layout (port of text.caption_lines)", () => {
  it("breaks lines at max_chars, long pauses and sentence ends", () => {
    const words = [
      { w: "Welcome", s: 0, e: 10 },
      { w: "to", s: 10, e: 14 },
      { w: "Champ", s: 14, e: 24 },
      { w: "Studio.", s: 24, e: 40 },
      { w: "Fast", s: 41, e: 50 },
      { w: "edits", s: 70, e: 80 }, // 20-frame pause → new line
    ];
    expect(captionLines(words, 32)).toEqual([[0, 1, 2, 3], [4], [5]]);
    expect(captionLines(words, 10)).toEqual([[0, 1], [2], [3], [4], [5]]);
  });
});

describe("normalization", () => {
  it("fills defaults like the pydantic model", () => {
    const tl = normalizeTimeline({ tracks: [{ id: "v1", kind: "video", clips: [{ id: "x", type: "text" } as never] } as never] });
    const c = tl.tracks[0]!.clips[0]!;
    expect(c.duration).toBe(30);
    expect(c.in).toBe(0);
    expect(c.style.font).toBe("Inter");
    expect(c.transform.scale).toBe(1);
    expect(tl.fps).toBe(30);
    expect(tl.loudness).toBe(-14);
  });

  it("keeps integer frames on new clips and picks the nearest aspect", () => {
    const c = makeClip({ type: "video", start: 10.6, duration: 20.2 });
    expect([c.start, c.duration]).toEqual([11, 20]);
    expect(aspectOf({ width: 1080, height: 1920 })).toBe("9:16");
    expect(aspectOf({ width: 1280, height: 720 })).toBe("16:9");
  });
});
