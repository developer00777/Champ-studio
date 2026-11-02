import { describe, expect, it } from "vitest";
import * as gfx from "../src/graphics";
import { defaultTextAnim, makeClip, normalizeTimeline, type Clip, type Timeline } from "../src/model";

function graphicTl(): { tl: Timeline; g: Clip } {
  const text = gfx.makeLayer("text", { text: "Hello" });
  text.id = "t";
  text.bind = { text: "{{name}}", "style.color": "{{accent}}" };
  const bar = gfx.makeLayer("rect");
  bar.id = "b";
  bar.bind = { "box.2": "{{pct}} * 0.008" };
  const g = gfx.makeGraphic(30, 90);
  g.id = "g";
  g.graphic!.layers = [bar, text];
  g.graphic!.controls = [
    { id: "name", label: "Name", kind: "text", value: "Priya", options: [] },
    { id: "accent", label: "Accent", kind: "color", value: "#FF0000", options: [] },
    { id: "pct", label: "Percent", kind: "number", value: 50, options: [] },
  ];
  const tl = normalizeTimeline({ tracks: [{ id: "v1", kind: "video", clips: [g] } as never] });
  return { tl, g: tl.tracks[0]!.clips[0]! };
}

describe("graphics (port of graphics.py)", () => {
  it("applies control bindings, including scaled box values", () => {
    const { g } = graphicTl();
    const [bar, text] = gfx.resolveLayers(g);
    expect(text!.text).toBe("Priya");
    expect(text!.style.color).toBe("#FF0000");
    expect(bar!.box[2]).toBeCloseTo(0.4);
    expect(text!.duration).toBe(90); // hold layers run to the graphic's end
  });

  it("formats numbers into text and keeps authored values for unset controls", () => {
    const l = makeClip({ type: "text", text: "x", bind: { text: "{{v}}%", "style.color": "{{c}}" } });
    const out = gfx.applyBindings(l, { v: 72, c: "" });
    expect(out.text).toBe("72%");
    expect(out.style.color).toBe("#FFFFFF");
  });

  it("counts numbers up keeping separators and decimals", () => {
    const co = { start: 0, duration: 30, delay: 0, ease: "linear" as const };
    expect(gfx.counterText("$4,200,000", co, 30)).toBe("$4,200,000");
    expect(gfx.counterText("$4,200,000", co, 15)).toBe("$2,100,000");
    expect(gfx.counterText("3.5x", co, 0)).toBe("0.0x");
  });

  it("staggers units and mirrors the outro like the engine", () => {
    const a = { ...defaultTextAnim(), by: "char" as const, duration: 10, stagger: 5, out: true };
    expect(gfx.unitProgress(a, 0, 3, 9, 100)).toBe(1);
    expect(gfx.unitProgress(a, 2, 3, 9, 100)).toBe(0);
    expect(gfx.unitProgress(a, 0, 3, 99, 100)).toBeLessThan(1);
  });

  it("edits layers: update, keyframe, reorder, duplicate, remove (pins are cleared)", () => {
    let { tl } = graphicTl();
    tl = gfx.updateLayer(tl, "g", "t", (l) => (l.name = "Name"));
    tl = gfx.setLayerKeyframe(tl, "g", "t", "opacity", 5, 0.5);
    expect(gfx.findLayer(tl, "g", "t")!.layer.keyframes.opacity).toEqual([{ f: 5, v: 0.5, ease: "ease" }]);
    tl = gfx.moveLayer(tl, "g", "t", -1);
    expect(gfx.findLayer(tl, "g", "t")!.index).toBe(0);
    tl = gfx.duplicateLayer(tl, "g", "t", "t2");
    tl = gfx.updateLayer(tl, "g", "b", (l) => (l.pin = { to: "t", pad: [0, 0, 0, 0], sides: ["left"] }));
    tl = gfx.removeLayer(tl, "g", "t");
    const layers = gfx.findLayer(tl, "g", "b")!.clip.graphic!.layers;
    expect(layers.map((l) => l.id)).toEqual(["t2", "b"]);
    expect(layers[1]!.pin).toBeNull();
  });

  it("exposes a property as a control and bakes it back in when removed", () => {
    let { tl } = graphicTl();
    const r = gfx.exposeProperty(tl, "g", "b", "shape.fill", "color", "Bar fill");
    tl = r.tl;
    expect(r.controlId).toBe("bar_fill");
    tl = gfx.setControlValue(tl, "g", "bar_fill", "#00FF00");
    expect(gfx.resolveLayers(gfx.findLayer(tl, "g", "b")!.clip)[0]!.shape.fill).toBe("#00FF00");
    tl = gfx.removeControl(tl, "g", "bar_fill");
    const b = gfx.findLayer(tl, "g", "b")!.layer;
    expect(b.shape.fill).toBe("#00FF00");
    expect(b.bind["shape.fill"]).toBeUndefined();
  });

  it("groups loose clips into a graphic and ungroups them again", () => {
    const t1 = makeClip({ type: "text", id: "a", start: 10, duration: 40, text: "A" });
    const s1 = makeClip({ type: "shape", id: "s", start: 20, duration: 30 });
    let tl = normalizeTimeline({ tracks: [{ id: "v1", kind: "video", clips: [s1] } as never, { id: "v2", kind: "video", clips: [t1] } as never] });
    const g = gfx.groupIntoGraphic(tl, ["a", "s"], "G");
    tl = g.tl;
    const clip = tl.tracks[0]!.clips[0]!;
    expect(clip.type).toBe("graphic");
    expect([clip.start, clip.duration]).toEqual([10, 40]);
    expect(clip.graphic!.layers.map((l) => [l.id, l.start])).toEqual([["s", 10], ["a", 0]]);
    const u = gfx.ungroupGraphic(tl, "G");
    expect(u.ids).toHaveLength(2);
    const starts = u.tl.tracks.flatMap((t) => t.clips.map((c) => c.start)).sort((x, y) => x - y);
    expect(starts).toEqual([10, 20]);
  });
});
