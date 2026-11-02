// Effect Controls / Inspector for the selected clip (or sequence settings).

import * as actions from "../actions";
import * as gfx from "../graphics";
import { media } from "../media";
import { defaultAdjust, defaultCounter, defaultTextAnim, findClip, parseTimecode, prop, timecode, type Clip, type Control, type Timeline } from "../model";
import * as ops from "../ops";
import { store } from "../store";
import { button, clear, h, icon } from "../ui";

const ANIMS = ["", "fade", "slide-up", "slide-down", "slide-left", "slide-right", "pop", "zoom-in", "zoom-out", "wipe", "typewriter", "blur"];
const TRANSITIONS = ["", "crossfade", "dip-black", "dip-white", "wipe-left", "wipe-right", "slide-left", "slide-right", "slide-up", "zoom"];
const WEIGHTS = [300, 400, 500, 600, 700, 800, 900];
const UNIT_ANIMS = ["fade", "rise", "drop", "pop", "slide-left", "slide-right", "reveal"];

type Sync = () => void;

export class Inspector {
  readonly el: HTMLElement;
  private body: HTMLElement;
  private syncs: Sync[] = [];
  private structure = "";

  constructor() {
    this.body = h("div", { class: "inspector-body" });
    this.el = h("div", { class: "inspector" }, this.body);
    store.on("selection", () => this.update());
    store.on("doc", () => this.update());
    store.on("readonly", () => this.update(true));
    media.on("fonts", () => this.update(true));
    let raf = 0;
    store.on("playhead", () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => this.syncAll());
    });
    this.update(true);
  }

  /** The selected clip, or the selected layer of a graphic (with its controls applied). */
  private clip(): Clip | null {
    const host = this.host();
    if (!host) return null;
    const lid = this.layerId();
    if (lid) {
      const layer = host.graphic?.layers.find((l) => l.id === lid);
      if (layer) return gfx.applyBindings(layer, gfx.controlValues(host));
    }
    return host;
  }

  private host(): Clip | null {
    return store.primary()?.clip ?? null;
  }

  private layerId(): string | null {
    const host = this.host();
    return host && store.layer?.clip === host.id ? store.layer.id : null;
  }

  /** Absolute timeline frame where the target's own time starts. */
  private origin(c: Clip): number {
    const host = this.host();
    return this.layerId() && host ? host.start + c.start : c.start;
  }

  /** Apply `fn` to the target (clip or graphic layer). Edits to control-bound layer
   *  properties update the control instead, like Premiere's master properties. */
  private upd(tl: Timeline, fn: (c: Clip) => void): Timeline {
    const host = this.host();
    const lid = this.layerId();
    if (!host) return tl;
    if (!lid) return ops.updateClip(tl, host.id, fn);
    const hit = gfx.findLayer(tl, host.id, lid);
    if (!hit) return tl;
    const values = gfx.controlValues(hit.clip);
    const shown = gfx.applyBindings(hit.layer, values);
    const after = structuredClone(shown);
    fn(after);
    let out = gfx.updateLayer(tl, host.id, lid, (raw) => {
      const bound = Object.keys(raw.bind);
      const keep = bound.map((p) => [p, structuredClone(gfx.readPath(raw, p))] as const);
      fn(raw);
      for (const [p, v] of keep) writePath(raw, p, v);
    });
    for (const [path, tmpl] of Object.entries(hit.layer.bind)) {
      const m = /^\s*\{\{\s*([a-zA-Z0-9_]+)\s*\}\}\s*$/.exec(tmpl);
      const before = gfx.readPath(shown, path);
      const now = gfx.readPath(after, path);
      if (m && JSON.stringify(before) !== JSON.stringify(now)) out = gfx.setControlValue(out, host.id, m[1]!, now as Control["value"]);
    }
    return out;
  }

  private targetKey(): string {
    return `${this.host()?.id ?? ""}/${this.layerId() ?? ""}`;
  }

  private update(force = false): void {
    const c = this.clip();
    const key = c
      ? [this.targetKey(), c.type, !!c.adjust, !!c.captions, !!c.shape.gradient, c.shape.kind, !!c.text_anim, !!c.counter, !!c.pin, c.graphic?.controls.map((k) => k.id + k.kind).join(","), c.graphic?.layers.length, Object.keys(c.keyframes).sort().join(","), store.selection.size, store.readOnly].join("|")
      : `none|${store.readOnly}|${store.view.markers.length}`;
    if (force || key !== this.structure) {
      this.structure = key;
      this.build(c);
    } else this.syncAll();
  }

  private syncAll(): void {
    for (const s of this.syncs) s();
  }

  private build(c: Clip | null): void {
    clear(this.body);
    this.syncs = [];
    if (!c) {
      this.buildSequence();
      return;
    }
    const n = store.selection.size;
    const lid = this.layerId();
    const host = this.host()!;
    if (lid) {
      this.body.append(
        h(
          "div",
          { class: "insp-crumb" },
          h("button", { class: "linklike", type: "button", onclick: () => store.selectLayer(host.id, null) }, host.name || "Graphic"),
          h("span", { class: "muted" }, " › layer"),
        ),
      );
    }
    this.body.append(
      h(
        "div",
        { class: "insp-head" },
        h("span", { class: `type-badge t-${c.type}` }, lid ? `${c.type} layer` : c.type),
        this.text("Name", (x) => x.name, (x, v) => (x.name = v), lid ? "Layer name" : "Clip name"),
      ),
    );
    if (n > 1) this.body.append(h("p", { class: "hint" }, `${n} clips selected — editing the first. Linked clips move and trim together.`));
    if (c.type === "graphic") this.buildGraphic(c);

    const timing = this.section("Timing");
    timing.append(
      this.tc(lid ? "Start (in graphic)" : "Start", (x) => x.start, (x, v) => (x.start = Math.max(0, v))),
      this.tc("Duration", (x) => x.duration, (x, v) => (x.duration = Math.max(1, v))),
    );
    if (lid) timing.append(this.check("Hold to end of graphic (keeps outro when the clip is trimmed)", (x) => x.hold, (x, v) => (x.hold = v)));
    if (c.type === "video" || c.type === "audio") {
      timing.append(
        this.tc("In (source)", (x) => x.in, (x, v) => (x.in = Math.max(0, v))),
        this.num("Speed", (x) => x.speed, (x, v) => (x.speed = v > 0 ? v : 1), { min: 0.1, max: 16, step: 0.05 }),
        this.check("Reverse", (x) => x.reverse, (x, v) => (x.reverse = v)),
        this.check("Freeze frame", (x) => x.freeze, (x, v) => (x.freeze = v)),
      );
    }
    timing.append(this.check("Enabled", (x) => x.enabled, (x, v) => (x.enabled = v)));

    if (c.type !== "audio") {
      const motion = this.section("Motion");
      if (c.type !== "adjustment") {
        motion.append(
          this.kf("Position X", "x", (x) => x.transform.x, (x, v) => (x.transform.x = v), { step: 0.005 }),
          this.kf("Position Y", "y", (x) => x.transform.y, (x, v) => (x.transform.y = v), { step: 0.005 }),
          this.kf("Scale", "scale", (x) => x.transform.scale, (x, v) => (x.transform.scale = v), { step: 0.01, min: 0 }),
          this.kf("Rotation", "rotation", (x) => x.transform.rotation, (x, v) => (x.transform.rotation = v), { step: 0.5 }),
        );
      }
      motion.append(this.kf("Opacity", "opacity", (x) => x.transform.opacity, (x, v) => (x.transform.opacity = v), { step: 0.01, min: 0, max: 1 }));
      const box = h("div", { class: "grid4" });
      (["X", "Y", "W", "H"] as const).forEach((l, i) =>
        box.append(this.num(`Box ${l}`, (x) => x.box[i] ?? 0, (x, v) => { const b = [...x.box]; b[i] = v; x.box = b; }, { step: 0.01, compact: true })),
      );
      motion.append(h("div", { class: "field-label" }, "Box (normalized x, y, w, h)"), box);
      if (c.type === "video" || c.type === "image") {
        motion.append(this.select("Fit", ["cover", "contain", "fill", "reframe"], (x) => x.fit, (x, v) => (x.fit = v as Clip["fit"])));
        if (c.fit === "reframe" || c.keyframes.crop_x) motion.append(this.kf("Reframe center", "crop_x", () => 0.5, () => undefined, { step: 0.01, min: 0, max: 1, kfOnly: true }));
      }

      const anim = this.section("Animation & transitions");
      anim.append(
        this.anim("Animate in", "anim_in", ANIMS),
        this.anim("Animate out", "anim_out", ANIMS),
        this.anim("Transition in", "transition_in", TRANSITIONS),
      );
    }

    if (c.type === "text" || c.type === "captions") this.buildText(c);
    if (c.type === "text") this.buildTextMotion(c);
    if (c.type === "shape") this.buildShape(c);
    if (lid) this.buildLayerLayout(c, host);
    if (c.type === "color") this.section("Color").append(this.color("Color", (x) => x.color, (x, v) => (x.color = v)));
    if (c.type === "video" || c.type === "image" || c.type === "adjustment" || c.type === "color") this.buildAdjust(c);
    if (c.type === "audio") this.buildAudio();
    this.syncAll();
  }

  private buildSequence(): void {
    const tl = store.view;
    this.body.append(h("div", { class: "insp-head" }, h("span", { class: "type-badge" }, "Sequence"), h("span", { class: "muted" }, `${tl.width}×${tl.height} · ${tl.fps} fps`)));
    this.body.append(h("p", { class: "hint" }, "Select a clip on the timeline to edit its effects. Double-click a marker or track name to rename it."));
    const s = this.section("Sequence");
    const seq = <T>(label: string, get: (t: Timeline) => T, set: (t: Timeline, v: T) => void, kind: "color" | "number") => {
      const input = h("input", { class: "input", type: kind === "color" ? "text" : "number", step: "0.5", "aria-label": label, disabled: store.readOnly });
      const sync = () => {
        if (document.activeElement !== input) input.value = String(get(store.view) ?? "");
      };
      input.addEventListener("change", () => {
        const v = (kind === "number" ? (input.value === "" ? null : Number(input.value)) : input.value) as T;
        store.apply(`Change ${label.toLowerCase()}`, (t) => {
          const out = structuredClone(t);
          set(out, v);
          return out;
        });
      });
      this.syncs.push(sync);
      return h("label", { class: "field" }, h("span", { class: "field-label" }, label), input);
    };
    s.append(
      seq("Background", (t) => t.background, (t, v) => (t.background = String(v)), "color"),
      seq("Loudness target (LUFS)", (t) => t.loudness ?? "", (t, v) => (t.loudness = v === null || Number.isNaN(Number(v)) ? null : Number(v)), "number"),
    );
    const m = this.section("Markers");
    if (!tl.markers.length) m.append(h("p", { class: "hint" }, "No markers. Press M to add one at the playhead."));
    tl.markers.forEach((mk, i) => {
      m.append(
        h(
          "div",
          { class: "marker-row" },
          h("button", { class: "linklike mono", type: "button", onclick: () => store.setPlayhead(mk.f) }, timecode(mk.f, tl.fps)),
          h("span", { class: "grow" }, mk.label || "—"),
          store.readOnly ? null : h("button", { class: "btn icon-btn btn-ghost", type: "button", "aria-label": "Delete marker", "data-tip": "Delete marker", onclick: () => store.apply("Delete marker", (t) => ops.removeMarker(t, i)) }, icon("close", 12)),
        ),
      );
    });
    this.syncAll();
  }

  private buildText(c: Clip): void {
    const s = this.section(c.type === "captions" ? "Captions" : "Text");
    if (c.type === "text") {
      const ta = h("textarea", { class: "input", rows: 3, "aria-label": "Text", disabled: store.readOnly });
      ta.addEventListener("input", () => this.change("Edit text", (x) => (x.text = ta.value), "text"));
      this.syncs.push(() => {
        const cc = this.clip();
        if (cc && document.activeElement !== ta) ta.value = cc.text;
      });
      s.append(h("label", { class: "field" }, h("span", { class: "field-label" }, "Content"), ta));
    } else {
      s.append(
        this.select("Mode", ["karaoke", "line", "word"], (x) => x.captions?.mode ?? "karaoke", (x, v) => x.captions && (x.captions.mode = v as "karaoke")),
        this.num("Max characters / line", (x) => x.captions?.max_chars ?? 32, (x, v) => x.captions && (x.captions.max_chars = Math.max(4, Math.round(v))), { step: 1 }),
        h("p", { class: "hint" }, `${c.captions?.words.length ?? 0} words. Use the AI panel → Captions to regenerate.`),
      );
    }
    const fams = media.families.length ? media.families : ["Inter"];
    const st = this.section("Text style");
    st.append(
      this.select("Font", fams, (x) => matchFamily(x.style.font, fams), (x, v) => (x.style.font = v)),
      this.select("Weight", WEIGHTS.map(String), (x) => String(x.style.weight), (x, v) => (x.style.weight = Number(v))),
      this.num("Size (px @1080p)", (x) => x.style.size, (x, v) => (x.style.size = Math.max(4, v)), { step: 1 }),
      this.color("Color", (x) => x.style.color, (x, v) => (x.style.color = v)),
      this.select("Align", ["left", "center", "right"], (x) => x.style.align, (x, v) => (x.style.align = v as "left")),
      this.select("Vertical", ["top", "middle", "bottom"], (x) => x.style.valign, (x, v) => (x.style.valign = v as "top")),
      this.num("Line height", (x) => x.style.line_height, (x, v) => (x.style.line_height = v), { step: 0.05 }),
      this.num("Letter spacing (em)", (x) => x.style.letter_spacing, (x, v) => (x.style.letter_spacing = v), { step: 0.01 }),
      this.check("Uppercase", (x) => x.style.uppercase, (x, v) => (x.style.uppercase = v)),
      this.select("Fit", ["shrink", "none"], (x) => x.style.fit, (x, v) => (x.style.fit = v as "shrink")),
      this.color("Stroke color", (x) => x.style.stroke_color ?? "", (x, v) => (x.style.stroke_color = v || null), true),
      this.num("Stroke width", (x) => x.style.stroke_width, (x, v) => (x.style.stroke_width = Math.max(0, v)), { step: 0.5 }),
      this.check("Shadow", (x) => x.style.shadow, (x, v) => (x.style.shadow = v)),
      this.color("Background", (x) => x.style.bg_color ?? "", (x, v) => (x.style.bg_color = v || null), true),
      this.num("Background padding (em)", (x) => x.style.bg_padding, (x, v) => (x.style.bg_padding = v), { step: 0.05 }),
      this.num("Background radius (em)", (x) => x.style.bg_radius, (x, v) => (x.style.bg_radius = v), { step: 0.05 }),
    );
    if (c.type === "captions") {
      st.append(
        this.color("Highlight color", (x) => x.style.highlight_color ?? "", (x, v) => (x.style.highlight_color = v || null), true),
        this.color("Highlight box", (x) => x.style.highlight_bg ?? "", (x, v) => (x.style.highlight_bg = v || null), true),
      );
    }
    if (store.brand?.styles && !store.readOnly) {
      const row = h("div", { class: "chip-row" });
      for (const [name, style] of Object.entries(store.brand.styles)) {
        row.append(h("button", { class: "chip", type: "button", "data-tip": `Apply ${store.brand.name} “${name}” style`, onclick: () => this.change(`Apply ${name} style`, (x) => Object.assign(x.style, style)) }, name.replace(/_/g, " ")));
      }
      st.append(h("div", { class: "field-label" }, `Brand styles · ${store.brand.name}`), row);
    }
  }

  /** Graphic clip: master properties (exposed controls) + layer list shortcut. */
  private buildGraphic(c: Clip): void {
    const s = this.section("Graphic");
    const controls = c.graphic?.controls ?? [];
    if (!controls.length) s.append(h("p", { class: "hint" }, "No exposed properties. In Essential Graphics → Edit, select a layer and use “Expose” to add fields here."));
    for (const k of controls) s.append(this.controlField(c, k));
    s.append(
      h(
        "div",
        { class: "row gap wrap" },
        button("Edit layers", () => store.showPanel("graphics"), "btn btn-sm", "grid", "Open Essential Graphics → Edit"),
        store.readOnly ? null : button("Ungroup", () => actions.ungroupGraphic(), "btn btn-sm", undefined, "Break the graphic into separate clips"),
      ),
      h("p", { class: "hint" }, `${c.graphic?.layers.length ?? 0} layers${c.graphic?.template ? ` · from template “${c.graphic.template}”` : ""}`),
    );
  }

  /** One input for an exposed control (works for the clip and its layers). */
  controlField(c: Clip, k: Control): HTMLElement {
    const set = (v: Control["value"]) => {
      const host = this.host();
      if (host) store.apply(`Change ${k.label || k.id}`, (tl) => gfx.setControlValue(tl, host.id, k.id, v), { coalesce: `${host.id}:ctl:${k.id}` });
    };
    const cur = () => this.host()?.graphic?.controls.find((x) => x.id === k.id)?.value;
    let input: HTMLElement;
    if (k.kind === "toggle") {
      const cb = h("input", { type: "checkbox", disabled: store.readOnly });
      cb.addEventListener("change", () => set(cb.checked));
      this.syncs.push(() => (cb.checked = !!cur()));
      return h("label", { class: "field check" }, cb, h("span", null, k.label || k.id));
    }
    if (k.kind === "choice") {
      const sel = h("select", { class: "input", disabled: store.readOnly, "aria-label": k.label }, k.options.map((o) => h("option", { value: o }, o)));
      sel.addEventListener("change", () => set(sel.value));
      this.syncs.push(() => (sel.value = String(cur() ?? "")));
      input = sel;
    } else if (k.kind === "color") {
      const sw = h("input", { type: "color", class: "swatch", disabled: store.readOnly, "aria-label": `${k.label} picker` });
      const tx = h("input", { class: "input mono", type: "text", disabled: store.readOnly, "aria-label": k.label });
      sw.addEventListener("input", () => set(sw.value.toUpperCase()));
      tx.addEventListener("change", () => /^#?[0-9a-f]{3,8}$/i.test(tx.value.trim()) && set(tx.value.trim().startsWith("#") ? tx.value.trim().toUpperCase() : `#${tx.value.trim().toUpperCase()}`));
      this.syncs.push(() => {
        const v = String(cur() ?? "");
        if (document.activeElement !== tx) tx.value = v;
        const m = /^#([0-9a-f]{6})/i.exec(v);
        sw.value = m ? `#${m[1]}` : "#000000";
      });
      input = h("div", { class: "field-row" }, sw, tx);
    } else if (k.kind === "number") {
      const num = h("input", { class: "input num", type: "number", step: "any", disabled: store.readOnly, "aria-label": k.label });
      if (k.min !== null && k.min !== undefined) num.min = String(k.min);
      if (k.max !== null && k.max !== undefined) num.max = String(k.max);
      num.addEventListener("input", () => num.value !== "" && set(Number(num.value)));
      this.syncs.push(() => document.activeElement !== num && (num.value = String(cur() ?? "")));
      input = num;
    } else if (k.kind === "media") {
      const imgs = [...media.assets.values()].filter((a) => a.kind === "image");
      const sel = h("select", { class: "input", disabled: store.readOnly, "aria-label": k.label }, h("option", { value: "" }, "Template default"), imgs.map((a) => h("option", { value: a.id }, String(a.params?.filename ?? a.prompt ?? a.id).slice(0, 48))));
      sel.addEventListener("change", () => set(sel.value ? { asset_id: sel.value } : null));
      this.syncs.push(() => {
        const v = cur();
        sel.value = v && typeof v === "object" ? (v as { asset_id: string }).asset_id : "";
      });
      input = sel;
    } else {
      const ta = k.multiline ? h("textarea", { class: "input", rows: 2, disabled: store.readOnly, "aria-label": k.label }) : h("input", { class: "input", type: "text", disabled: store.readOnly, "aria-label": k.label });
      ta.addEventListener("input", () => set((ta as HTMLInputElement).value));
      this.syncs.push(() => document.activeElement !== ta && ((ta as HTMLInputElement).value = String(cur() ?? "")));
      input = ta;
    }
    void c;
    return h("label", { class: "field" }, h("span", { class: "field-label" }, k.label || k.id), input);
  }

  /** Per-unit text animator and number counter (any text clip or layer). */
  private buildTextMotion(c: Clip): void {
    const s = this.section("Text animator");
    s.append(this.check("Animate by character / word / line", (x) => !!x.text_anim, (x, v) => (x.text_anim = v ? x.text_anim ?? defaultTextAnim() : null)));
    if (c.text_anim) {
      s.append(
        this.select("Unit", ["char", "word", "line"], (x) => x.text_anim?.by ?? "word", (x, v) => x.text_anim && (x.text_anim.by = v as "word")),
        this.select("Style", UNIT_ANIMS, (x) => x.text_anim?.type ?? "rise", (x, v) => x.text_anim && (x.text_anim.type = v as "rise")),
        this.select("Order", ["forward", "reverse", "center", "random"], (x) => x.text_anim?.order ?? "forward", (x, v) => x.text_anim && (x.text_anim.order = v as "forward")),
        this.num("Duration per unit (frames)", (x) => x.text_anim?.duration ?? 10, (x, v) => x.text_anim && (x.text_anim.duration = Math.max(1, Math.round(v))), { step: 1, min: 1 }),
        this.num("Stagger (frames)", (x) => x.text_anim?.stagger ?? 2, (x, v) => x.text_anim && (x.text_anim.stagger = Math.max(0, Math.round(v))), { step: 1, min: 0 }),
        this.num("Delay (frames)", (x) => x.text_anim?.delay ?? 0, (x, v) => x.text_anim && (x.text_anim.delay = Math.max(0, Math.round(v))), { step: 1, min: 0 }),
        this.check("Animate out too (mirrored at the end)", (x) => !!x.text_anim?.out, (x, v) => x.text_anim && (x.text_anim.out = v)),
      );
    }
    const k = this.section("Number counter");
    k.append(this.check("Count up the first number in the text", (x) => !!x.counter, (x, v) => (x.counter = v ? x.counter ?? defaultCounter() : null)));
    if (c.counter) {
      k.append(
        this.num("From", (x) => x.counter?.start ?? 0, (x, v) => x.counter && (x.counter.start = v), { step: 1 }),
        this.num("Duration (frames)", (x) => x.counter?.duration ?? 30, (x, v) => x.counter && (x.counter.duration = Math.max(1, Math.round(v))), { step: 1, min: 1 }),
        this.num("Delay (frames)", (x) => x.counter?.delay ?? 0, (x, v) => x.counter && (x.counter.delay = Math.max(0, Math.round(v))), { step: 1, min: 0 }),
        this.select("Easing", ["linear", "ease", "ease-in", "ease-out"], (x) => x.counter?.ease ?? "ease-out", (x, v) => x.counter && (x.counter.ease = v as "ease")),
      );
    }
  }

  /** Graphic layer: responsive pin + mask + exposing properties. */
  private buildLayerLayout(c: Clip, host: Clip): void {
    const others = (host.graphic?.layers ?? []).filter((l) => l.id !== c.id);
    const s = this.section("Responsive design");
    s.append(
      this.select("Pin to layer", ["", ...others.map((l) => l.id)], (x) => x.pin?.to ?? "", (x, v) => (x.pin = v ? { to: v, pad: x.pin?.pad ?? [0.02, 0.012, 0.02, 0.012], sides: x.pin?.sides ?? ["left", "top", "right", "bottom"] } : null), (id) => (id ? others.find((l) => l.id === id)?.name || id : "None")),
    );
    if (c.pin) {
      const sides = h("div", { class: "row gap wrap" });
      for (const side of ["left", "top", "right", "bottom"] as const) {
        sides.append(this.check(side, (x) => !!x.pin?.sides.includes(side), (x, v) => x.pin && (x.pin.sides = v ? [...new Set([...x.pin.sides, side])] : x.pin.sides.filter((q) => q !== side))));
      }
      const pad = h("div", { class: "grid4" });
      (["L", "T", "R", "B"] as const).forEach((l, i) => pad.append(this.num(`Pad ${l}`, (x) => x.pin?.pad[i] ?? 0, (x, v) => { if (!x.pin) return; const p = [...x.pin.pad]; p[i] = v; x.pin.pad = p; }, { step: 0.005, compact: true })));
      s.append(h("div", { class: "field-label" }, "Follow edges"), sides, h("div", { class: "field-label" }, "Padding (canvas units)"), pad, h("p", { class: "hint" }, "The layer's pinned edges track the other layer's content, so a background bar grows with the text typed into it."));
    }
    s.append(this.select("Mask", ["", "below", "below-inverse"], (x) => x.mask ?? "", (x, v) => (x.mask = (v || null) as Clip["mask"]), (v) => ({ "": "None", below: "Show only over layers below", "below-inverse": "Cut out layers below" })[v] ?? v));

    if (store.readOnly) return;
    const ex = this.section("Exposed properties");
    const props: Array<[string, string, Control["kind"]]> =
      c.type === "text"
        ? [["text", "Text", "text"], ["style.color", "Text color", "color"], ["style.size", "Text size", "number"], ["style.bg_color", "Text background", "color"]]
        : c.type === "shape"
          ? [["shape.fill", "Fill", "color"], ["shape.stroke", "Stroke", "color"], ["gradient.0", "Gradient start", "color"], ["gradient.1", "Gradient end", "color"]]
          : c.type === "image"
            ? [["asset_id", "Image", "media"]]
            : c.type === "color"
              ? [["color", "Color", "color"]]
              : [];
    props.push(["enabled", "Visible", "toggle"], ["transform.opacity", "Opacity", "number"]);
    const raw = host.graphic?.layers.find((l) => l.id === c.id);
    for (const [path, label, kind] of props) {
      const bound = raw?.bind[path];
      ex.append(
        h(
          "div",
          { class: "expose-row" },
          h("span", { class: "grow" }, label),
          bound
            ? h("span", { class: "chip on", "data-tip": `Driven by ${bound}` }, bound.replace(/[{}]/g, ""))
            : button("Expose", () => {
                const name = `${c.name || c.type} ${label}`.trim();
                store.apply(`Expose ${label.toLowerCase()}`, (tl) => gfx.exposeProperty(tl, host.id, c.id, path, kind, name).tl);
              }, "btn btn-sm btn-ghost", undefined, "Add to the graphic's master properties"),
        ),
      );
    }
  }

  private buildShape(c: Clip): void {
    const s = this.section("Shape");
    s.append(
      this.select("Kind", ["rect", "ellipse", "line", "triangle", "polygon", "star"], (x) => x.shape.kind, (x, v) => (x.shape.kind = v as "rect")),
      this.color("Fill", (x) => x.shape.fill ?? "", (x, v) => (x.shape.fill = v || null), true),
      this.num("Corner radius", (x) => x.shape.radius, (x, v) => (x.shape.radius = Math.max(0, Math.min(0.5, v))), { step: 0.01, min: 0, max: 0.5 }),
      this.color("Stroke", (x) => x.shape.stroke ?? "", (x, v) => (x.shape.stroke = v || null), true),
      this.num("Stroke width", (x) => x.shape.stroke_width, (x, v) => (x.shape.stroke_width = Math.max(0, v)), { step: 0.5 }),
      this.check("Gradient", (x) => !!x.shape.gradient, (x, v) => (x.shape.gradient = v ? [x.shape.fill ?? "#FF6B35", "#12151C"] : null)),
    );
    if (c.shape.kind === "polygon" || c.shape.kind === "star") s.append(this.num(c.shape.kind === "star" ? "Points" : "Sides", (x) => x.shape.sides, (x, v) => (x.shape.sides = Math.max(3, Math.min(24, Math.round(v)))), { step: 1, min: 3, max: 24 }));
    if (c.shape.gradient) {
      s.append(
        this.color("Gradient start", (x) => x.shape.gradient?.[0] ?? "", (x, v) => x.shape.gradient && (x.shape.gradient = [v, x.shape.gradient[1] ?? v])),
        this.color("Gradient end", (x) => x.shape.gradient?.[1] ?? "", (x, v) => x.shape.gradient && (x.shape.gradient = [x.shape.gradient[0] ?? v, v])),
        this.num("Gradient angle (°)", (x) => x.shape.gradient_angle, (x, v) => (x.shape.gradient_angle = v), { step: 5, min: 0, max: 360, range: true }),
      );
    }
  }

  private buildAdjust(c: Clip): void {
    const s = this.section("Color (Lumetri basic)");
    s.append(this.check("Enable color adjustments", (x) => !!x.adjust, (x, v) => (x.adjust = v ? x.adjust ?? defaultAdjust() : null)));
    if (!c.adjust) return;
    const a = (k: "exposure" | "contrast" | "saturation" | "brightness" | "temperature" | "tint", label: string, step: number, min: number, max: number, keyframe: boolean) =>
      keyframe
        ? this.kf(label, k, (x) => x.adjust?.[k] ?? 0, (x, v) => x.adjust && (x.adjust[k] = v), { step, min, max, range: true })
        : this.num(label, (x) => x.adjust?.[k] ?? 0, (x, v) => x.adjust && (x.adjust[k] = v), { step, min, max, range: true });
    s.append(
      a("exposure", "Exposure (stops)", 0.05, -4, 4, true),
      a("contrast", "Contrast", 0.01, -1, 1, true),
      a("saturation", "Saturation", 0.01, 0, 2, true),
      a("brightness", "Brightness", 0.01, -1, 1, true),
      a("temperature", "Temperature", 0.01, -1, 1, false),
      a("tint", "Tint", 0.01, -1, 1, false),
    );
    const luts = [...media.assets.values()].filter((x) => /\.cube$/i.test(String(x.params?.filename ?? x.filename ?? "")) || x.mime === "application/octet-stream");
    s.append(
      this.select("LUT", ["", ...luts.map((l) => l.id)], (x) => x.adjust?.lut_asset_id ?? "", (x, v) => x.adjust && (x.adjust.lut_asset_id = v || null), (id) => (id ? String(media.assets.get(id)?.params?.filename ?? id) : "None")),
      this.num("LUT strength", (x) => x.adjust?.lut_strength ?? 1, (x, v) => x.adjust && (x.adjust.lut_strength = v), { step: 0.01, min: 0, max: 1, range: true }),
      h("p", { class: "hint" }, "Temperature, tint and LUTs are approximate while playing; the paused “Exact frame” is the server render."),
    );
  }

  private buildAudio(): void {
    const s = this.section("Audio");
    s.append(
      this.kf("Volume (gain)", "volume", (x) => x.volume, (x, v) => (x.volume = Math.max(0, v)), { step: 0.01, min: 0, max: 4, range: true }),
      this.num("Fade in (frames)", (x) => x.fade_in, (x, v) => (x.fade_in = Math.max(0, Math.round(v))), { step: 1, min: 0 }),
      this.num("Fade out (frames)", (x) => x.fade_out, (x, v) => (x.fade_out = Math.max(0, Math.round(v))), { step: 1, min: 0 }),
      this.select("Role", ["", "dialogue", "vo", "music", "sfx", "ambience"], (x) => x.role ?? "", (x, v) => (x.role = (v || null) as Clip["role"])),
      this.num("Duck under speech (dB)", (x) => x.duck_db ?? 0, (x, v) => (x.duck_db = v ? Math.abs(v) : null), { step: 1, min: 0, max: 40 }),
    );
  }

  // ── field builders ──

  private section(title: string): HTMLElement {
    const body = h("div", { class: "section-body" });
    const det = h("details", { class: "section", open: true }, h("summary", null, title), body);
    this.body.append(det);
    return body;
  }

  /** Apply a change to the primary clip as one (coalescing) command. */
  private change(label: string, fn: (c: Clip) => void, key = label): void {
    if (!this.clip()) return;
    store.apply(label, (tl) => this.upd(tl, fn), { coalesce: `${this.targetKey()}:${key}` });
  }

  private text(label: string, get: (c: Clip) => string, set: (c: Clip, v: string) => void, aria: string): HTMLElement {
    const input = h("input", { class: "input input-name", type: "text", "aria-label": aria, disabled: store.readOnly, placeholder: label });
    input.addEventListener("input", () => this.change(`Rename clip`, (c) => set(c, input.value), "name"));
    this.syncs.push(() => {
      const c = this.clip();
      if (c && document.activeElement !== input) input.value = get(c);
    });
    return input;
  }

  private tc(label: string, get: (c: Clip) => number, set: (c: Clip, v: number) => void): HTMLElement {
    const input = h("input", { class: "input mono", type: "text", "aria-label": label, disabled: store.readOnly });
    const commit = () => {
      const v = parseTimecode(input.value, store.fps);
      const c = this.clip();
      if (v === null || !c) return;
      store.apply(`Change ${label.toLowerCase()}`, (tl) => this.upd(tl, (x) => set(x, v)));
    };
    input.addEventListener("change", commit);
    input.addEventListener("keydown", (e) => e.key === "Enter" && input.blur());
    this.syncs.push(() => {
      const c = this.clip();
      if (c && document.activeElement !== input) input.value = timecode(get(c), store.fps);
    });
    return h("label", { class: "field" }, h("span", { class: "field-label" }, label), input);
  }

  private num(label: string, get: (c: Clip) => number, set: (c: Clip, v: number) => void, o: { min?: number; max?: number; step?: number; compact?: boolean; range?: boolean } = {}): HTMLElement {
    const input = h("input", { class: "input num", type: "number", step: String(o.step ?? 0.01), "aria-label": label, disabled: store.readOnly });
    if (o.min !== undefined) input.min = String(o.min);
    if (o.max !== undefined) input.max = String(o.max);
    const range = o.range && o.min !== undefined && o.max !== undefined ? h("input", { class: "range", type: "range", min: String(o.min), max: String(o.max), step: String(o.step ?? 0.01), "aria-label": `${label} slider`, disabled: store.readOnly }) : null;
    const onInput = (v: number) => {
      if (Number.isNaN(v)) return;
      this.change(`Change ${label.toLowerCase()}`, (c) => set(c, v), label);
    };
    input.addEventListener("input", () => onInput(parseFloat(input.value)));
    range?.addEventListener("input", () => onInput(parseFloat(range.value)));
    this.syncs.push(() => {
      const c = this.clip();
      if (!c) return;
      const v = round(get(c));
      if (document.activeElement !== input) input.value = String(v);
      if (range && document.activeElement !== range) range.value = String(v);
    });
    return h("label", { class: `field${o.compact ? " compact" : ""}` }, h("span", { class: "field-label" }, label), h("div", { class: "field-row" }, range, input));
  }

  /** Keyframeable number with a diamond toggle and prev/next keyframe navigation. */
  private kf(label: string, name: string, base: (c: Clip) => number, setBase: (c: Clip, v: number) => void, o: { min?: number; max?: number; step?: number; range?: boolean; kfOnly?: boolean } = {}): HTMLElement {
    const input = h("input", { class: "input num", type: "number", step: String(o.step ?? 0.01), "aria-label": label, disabled: store.readOnly });
    const range = o.range && o.min !== undefined && o.max !== undefined ? h("input", { class: "range", type: "range", min: String(o.min), max: String(o.max), step: String(o.step ?? 0.01), "aria-label": `${label} slider`, disabled: store.readOnly }) : null;
    const diamond = h("button", { class: "kf-btn", type: "button", "aria-label": `Toggle ${label} keyframe at playhead`, "data-tip": "Toggle animation keyframe at playhead", disabled: store.readOnly }, icon("diamond", 12));
    const prev = h("button", { class: "kf-nav", type: "button", "aria-label": "Previous keyframe", "data-tip": "Previous keyframe" }, icon("chevl", 10));
    const next = h("button", { class: "kf-nav", type: "button", "aria-label": "Next keyframe", "data-tip": "Next keyframe" }, icon("chevr", 10));
    const rel = (c: Clip) => Math.max(0, Math.min(c.duration - 1, store.playhead - this.origin(c)));
    const current = (c: Clip) => prop(c, name, store.playhead - this.origin(c), base(c));
    const setKf = (tl: Timeline, c: Clip, f: number, v: number) => {
      const lid = this.layerId();
      const host = this.host()!;
      return lid ? gfx.setLayerKeyframe(tl, host.id, lid, name, f, v) : ops.setKeyframe(tl, c.id, name, f, v);
    };
    const removeKf = (tl: Timeline, c: Clip, f: number) => {
      const lid = this.layerId();
      const host = this.host()!;
      return lid ? gfx.removeLayerKeyframe(tl, host.id, lid, name, f) : ops.removeKeyframe(tl, c.id, name, f);
    };
    const setValue = (v: number) => {
      const c = this.clip();
      if (!c || Number.isNaN(v)) return;
      const animated = (c.keyframes[name]?.length ?? 0) > 0;
      if (animated || o.kfOnly) store.apply(`Keyframe ${label.toLowerCase()}`, (tl) => setKf(tl, c, rel(c), v), { coalesce: `${this.targetKey()}:kf:${name}:${rel(c)}` });
      else this.change(`Change ${label.toLowerCase()}`, (x) => setBase(x, v), name);
    };
    input.addEventListener("input", () => setValue(parseFloat(input.value)));
    range?.addEventListener("input", () => setValue(parseFloat(range.value)));
    diamond.addEventListener("click", () => {
      const c = this.clip();
      if (!c) return;
      const f = rel(c);
      const has = c.keyframes[name]?.some((k) => k.f === f);
      if (has) store.apply(`Remove ${label.toLowerCase()} keyframe`, (tl) => removeKf(tl, c, f));
      else store.apply(`Add ${label.toLowerCase()} keyframe`, (tl) => setKf(tl, c, f, current(c)));
    });
    const jump = (dir: 1 | -1) => {
      const c = this.clip();
      if (!c) return;
      const f = store.playhead - this.origin(c);
      const list = (c.keyframes[name] ?? []).map((k) => k.f).sort((a, b) => a - b);
      const t = dir > 0 ? list.find((k) => k > f) : [...list].reverse().find((k) => k < f);
      if (t !== undefined) store.setPlayhead(this.origin(c) + t);
    };
    prev.addEventListener("click", () => jump(-1));
    next.addEventListener("click", () => jump(1));
    this.syncs.push(() => {
      const c = this.clip();
      if (!c) return;
      const kfs = c.keyframes[name] ?? [];
      const v = round(current(c));
      if (document.activeElement !== input) input.value = String(v);
      if (range && document.activeElement !== range) range.value = String(v);
      const o0 = this.origin(c);
      const inside = store.playhead >= o0 && store.playhead < o0 + c.duration;
      diamond.classList.toggle("animated", kfs.length > 0);
      diamond.classList.toggle("on", kfs.some((k) => k.f === store.playhead - o0));
      diamond.disabled = store.readOnly || !inside;
      prev.style.visibility = next.style.visibility = kfs.length ? "visible" : "hidden";
    });
    return h("div", { class: "field kf-field" }, h("span", { class: "field-label" }, label), h("div", { class: "field-row" }, prev, diamond, next, range, input));
  }

  private check(label: string, get: (c: Clip) => boolean, set: (c: Clip, v: boolean) => void): HTMLElement {
    const input = h("input", { type: "checkbox", disabled: store.readOnly });
    input.addEventListener("change", () => {
      const c = this.clip();
      if (c) store.apply(`${input.checked ? "Enable" : "Disable"} ${label.toLowerCase()}`, (tl) => this.upd(tl, (x) => set(x, input.checked)));
    });
    this.syncs.push(() => {
      const c = this.clip();
      if (c) input.checked = get(c);
    });
    return h("label", { class: "field check" }, input, h("span", null, label));
  }

  private select(label: string, options: string[], get: (c: Clip) => string, set: (c: Clip, v: string) => void, name: (v: string) => string = (v) => v || "None"): HTMLElement {
    const sel = h("select", { class: "input", "aria-label": label, disabled: store.readOnly }, options.map((o) => h("option", { value: o }, name(o))));
    sel.addEventListener("change", () => {
      const c = this.clip();
      if (c) store.apply(`Change ${label.toLowerCase()}`, (tl) => this.upd(tl, (x) => set(x, sel.value)));
    });
    this.syncs.push(() => {
      const c = this.clip();
      if (!c) return;
      const v = get(c);
      if (![...sel.options].some((o) => o.value === v)) sel.append(h("option", { value: v }, name(v)));
      sel.value = v;
    });
    return h("label", { class: "field" }, h("span", { class: "field-label" }, label), sel);
  }

  private color(label: string, get: (c: Clip) => string, set: (c: Clip, v: string) => void, nullable = false): HTMLElement {
    const swatch = h("input", { type: "color", class: "swatch", "aria-label": `${label} picker`, disabled: store.readOnly });
    const input = h("input", { class: "input mono", type: "text", "aria-label": label, placeholder: nullable ? "none" : "#RRGGBB", disabled: store.readOnly });
    const apply = (v: string) => this.change(`Change ${label.toLowerCase()}`, (c) => set(c, v), label);
    swatch.addEventListener("input", () => {
      const cur = input.value;
      const alpha = /^#[0-9a-f]{8}$/i.test(cur) ? cur.slice(7) : "";
      apply(swatch.value.toUpperCase() + alpha);
    });
    input.addEventListener("change", () => {
      const v = input.value.trim();
      if (!v && nullable) apply("");
      else if (/^#?[0-9a-f]{3,8}$/i.test(v)) apply(v.startsWith("#") ? v.toUpperCase() : `#${v.toUpperCase()}`);
    });
    this.syncs.push(() => {
      const c = this.clip();
      if (!c) return;
      const v = get(c) || "";
      if (document.activeElement !== input) input.value = v;
      const m = /^#([0-9a-f]{6})/i.exec(v);
      swatch.value = m ? `#${m[1]}` : "#000000";
    });
    return h("label", { class: "field" }, h("span", { class: "field-label" }, label), h("div", { class: "field-row" }, swatch, input));
  }

  private anim(label: string, key: "anim_in" | "anim_out" | "transition_in", types: string[]): HTMLElement {
    const sel = h("select", { class: "input", "aria-label": `${label} type`, disabled: store.readOnly }, types.map((t) => h("option", { value: t }, t || "None")));
    const dur = h("input", { class: "input num", type: "number", min: "1", step: "1", "aria-label": `${label} duration (frames)`, disabled: store.readOnly, "data-tip": "Duration in frames" });
    const commit = () => {
      const c = this.clip();
      if (!c) return;
      const t = sel.value;
      const d = Math.max(1, Math.round(Number(dur.value) || 12));
      store.apply(`Change ${label.toLowerCase()}`, (tl) => this.upd(tl, (x) => (x[key] = t ? { type: t, duration: d } : null)), { coalesce: `${this.targetKey()}:${key}` });
    };
    sel.addEventListener("change", commit);
    dur.addEventListener("input", commit);
    this.syncs.push(() => {
      const c = this.clip();
      if (!c) return;
      const a = c[key];
      sel.value = a?.type ?? "";
      if (document.activeElement !== dur) dur.value = String(a?.duration ?? (key === "transition_in" ? 15 : 12));
      dur.disabled = store.readOnly || !a;
    });
    return h("label", { class: "field" }, h("span", { class: "field-label" }, label), h("div", { class: "field-row" }, sel, dur));
  }
}

function writePath(obj: Clip, path: string, v: unknown): void {
  if (path.startsWith("gradient.")) {
    const i = Number(path.split(".")[1]);
    if (obj.shape.gradient && i < obj.shape.gradient.length) obj.shape.gradient[i] = String(v);
    return;
  }
  const parts = path.split(".");
  let node = obj as unknown as Record<string, unknown>;
  for (const p of parts.slice(0, -1)) {
    if (!node[p] || typeof node[p] !== "object") node[p] = {};
    node = node[p] as Record<string, unknown>;
  }
  node[parts[parts.length - 1]!] = v;
}

function round(v: number): number {
  return Math.round(v * 10000) / 10000;
}

function matchFamily(font: string, fams: string[]): string {
  const n = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
  return fams.find((f) => n(f) === n(font)) ?? font;
}

export function selectedClip(tl: Timeline): Clip | null {
  for (const id of store.selection) {
    const c = findClip(tl, id)?.clip;
    if (c) return c;
  }
  return null;
}
