// Titles & graphics (Essential Graphics-style): brand-utility templates with
// previews; click inserts the compiled clips at the playhead on free tracks.
// Also hosts the brand kit selector used for new text styles.

import { get, post, type Brand, type GraphicResult, type TemplateSummary } from "../api";
import * as actions from "../actions";
import { aspectOf, clipEnd, normalizeClip, type Clip, type Timeline } from "../model";
import * as ops from "../ops";
import { store } from "../store";
import { button, clear, h, icon, modal, toast } from "../ui";

const BRAND_KEY = "champ-editor-brand";

export class GraphicsPanel {
  readonly el: HTMLElement;
  private grid: HTMLElement;
  private brandSel: HTMLSelectElement;
  private brands: Brand[] = [];
  private templates: TemplateSummary[] = [];
  private loaded = false;

  constructor() {
    this.grid = h("div", { class: "template-grid" });
    this.brandSel = h("select", { class: "input", "aria-label": "Brand kit" });
    this.brandSel.addEventListener("change", () => this.setBrand(this.brandSel.value));
    this.el = h(
      "div",
      { class: "graphics-panel" },
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Brand kit (new titles & captions use its styles)"), this.brandSel),
      h(
        "div",
        { class: "row gap wrap" },
        button("Title", () => actions.addTitle(), "btn btn-sm", "text", "Add title at playhead (T)"),
        button("Shape", () => actions.addShape(), "btn btn-sm", "grid"),
        button("Color matte", () => actions.addColorMatte(), "btn btn-sm", "image"),
        button("Adjustment layer", () => actions.addAdjustmentLayer(), "btn btn-sm", "sparkle"),
      ),
      h("h3", { class: "dlg-h" }, "Templates"),
      this.grid,
    );
    store.on("project", () => this.loaded && this.renderGrid());
  }

  async load(): Promise<void> {
    if (this.loaded) return;
    this.loaded = true;
    clear(this.grid);
    for (let i = 0; i < 4; i++) this.grid.append(h("div", { class: "template-card skeleton" }));
    try {
      const b = await get<{ brands: Brand[] }>("/api/brands");
      this.brands = b.brands;
    } catch {
      this.brands = [];
    }
    clear(this.brandSel);
    this.brandSel.append(h("option", { value: "" }, "None"), ...this.brands.map((b) => h("option", { value: b.id }, b.name)));
    const saved = (store.doc.meta.brand_kit_id as string | undefined) ?? store.project?.brand_kit_id ?? safeGet(BRAND_KEY) ?? "";
    this.brandSel.value = this.brands.some((b) => b.id === saved) ? saved : "";
    this.setBrand(this.brandSel.value, false);
    try {
      const t = await get<{ templates: TemplateSummary[] }>("/api/templates?category=brand-utility");
      this.templates = t.templates;
    } catch (e) {
      clear(this.grid);
      this.grid.append(h("div", { class: "empty" }, h("p", null, "Couldn't load templates."), h("p", { class: "muted" }, (e as Error).message)));
      return;
    }
    this.renderGrid();
  }

  /** Load just the brand kits (so titles get brand styles before the panel is opened). */
  async loadBrandOnly(): Promise<void> {
    try {
      const saved = (store.doc.meta.brand_kit_id as string | undefined) ?? store.project?.brand_kit_id ?? safeGet(BRAND_KEY);
      if (!saved) return;
      const b = await get<{ brands: Brand[] }>("/api/brands");
      this.brands = b.brands;
      store.brand = this.brands.find((x) => x.id === saved) ?? null;
    } catch {
      /* brands unavailable */
    }
  }

  private setBrand(id: string, persist = true): void {
    store.brand = this.brands.find((b) => b.id === id) ?? null;
    if (persist) {
      try {
        localStorage.setItem(BRAND_KEY, id);
      } catch {
        /* storage blocked */
      }
      if (!store.readOnly && store.doc.meta.brand_kit_id !== (id || undefined)) {
        store.apply("Set brand kit", (tl) => ({ ...tl, meta: { ...tl.meta, brand_kit_id: id || undefined } }));
      }
    }
    store.emit("selection"); // refresh inspector brand chips
    this.renderGrid();
  }

  private renderGrid(): void {
    if (!this.templates.length && this.loaded) {
      clear(this.grid);
      this.grid.append(h("div", { class: "empty" }, icon("grid", 24), h("p", null, "No brand-utility templates."), h("p", { class: "muted" }, "Add title, lower-third or end-card templates in Champ Studio's template library.")));
      return;
    }
    clear(this.grid);
    const aspect = aspectOf(store.doc);
    for (const t of this.templates) {
      const brand = this.brandSel.value ? `&brand=${encodeURIComponent(this.brandSel.value)}` : "";
      const img = h("img", { src: `/api/templates/${t.id}/preview.jpg?aspect=${encodeURIComponent(aspect)}${brand}`, alt: "", loading: "lazy" });
      img.addEventListener("error", () => img.replaceWith(h("div", { class: "template-fallback" }, icon("grid", 22))));
      this.grid.append(
        h(
          "button",
          { class: "template-card", type: "button", "data-tip": t.description ?? t.name, onclick: () => void this.customize(t) },
          h("div", { class: "template-thumb" }, img),
          h("span", { class: "template-name" }, t.name),
        ),
      );
    }
  }

  /** Ask for slot values, then insert. */
  private async customize(t: TemplateSummary): Promise<void> {
    if (store.readOnly) {
      toast(store.readOnlyReason || "Read-only", "warn");
      return;
    }
    const slots = t.slots ?? [];
    if (!slots.length) return this.insert(t, {});
    const m = modal(`Insert “${t.name}”`);
    const inputs = new Map<string, HTMLInputElement>();
    for (const s of slots) {
      const input = h("input", { class: "input", type: "text", placeholder: "Leave empty for the template default", "aria-label": s });
      inputs.set(s, input);
      m.body.append(h("label", { class: "field" }, h("span", { class: "field-label" }, s.replace(/_/g, " ")), input));
    }
    m.footer.append(
      button("Cancel", () => m.close()),
      button("Insert at playhead", () => {
        const values: Record<string, string> = {};
        for (const [k, v] of inputs) if (v.value.trim()) values[k] = v.value.trim();
        m.close();
        void this.insert(t, values);
      }, "btn btn-primary"),
    );
  }

  private async insert(t: TemplateSummary, values: Record<string, string>): Promise<void> {
    try {
      const r = await post<GraphicResult>(`/api/templates/${t.id}/graphic`, { values, brand_kit_id: this.brandSel.value || null, aspect: aspectOf(store.doc), start: store.playhead });
      const ids: string[] = [];
      store.apply(`Insert ${t.name}`, (tl) => insertGraphic(tl, r, ids));
      store.select(ids);
      toast(`Inserted ${t.name}`, "success", 2500);
    } catch (e) {
      toast(`Couldn't insert ${t.name}: ${(e as Error).message}`, "error");
    }
  }
}

/** Place each template track's clips on a free video track (above existing picture), adding tracks as needed. */
export function insertGraphic(tl: Timeline, r: GraphicResult, ids: string[]): Timeline {
  let out = tl;
  const used = new Set<string>();
  for (const tr of r.tracks) {
    const clips: Clip[] = tr.clips.map((c) => {
      const n = normalizeClip(structuredClone(c));
      n.id = `${n.id}_${Math.random().toString(36).slice(2, 7)}`;
      ids.push(n.id);
      return n;
    });
    if (!clips.length) continue;
    const s = Math.min(...clips.map((c) => c.start));
    const e = Math.max(...clips.map(clipEnd));
    const vids = out.tracks.filter((t) => t.kind === "video");
    let topUsed = -1;
    vids.forEach((t, i) => {
      if (t.clips.some((c) => c.start < e && clipEnd(c) > s)) topUsed = i;
    });
    let tid: string | null = null;
    for (let i = topUsed + 1; i < vids.length; i++) {
      const t = vids[i]!;
      if (!t.locked && !used.has(t.id) && t.clips.every((c) => clipEnd(c) <= s || c.start >= e)) {
        tid = t.id;
        break;
      }
    }
    if (!tid) {
      const a = ops.addTrack(out, "video");
      out = a.tl;
      tid = a.id;
    }
    used.add(tid);
    out = ops.overwrite(out, clips.map((clip) => ({ trackId: tid!, clip })));
  }
  return out;
}

function safeGet(k: string): string | null {
  try {
    return localStorage.getItem(k);
  } catch {
    return null;
  }
}
