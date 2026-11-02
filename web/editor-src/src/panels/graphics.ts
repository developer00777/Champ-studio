// Essential Graphics panel (Premiere-style).
//   Browse: motion-graphics templates with animated hover previews; click
//           drops ONE graphic clip at the playhead whose fields are controls.
//   Edit:   the selected graphic's layer stack (add / reorder / hide / lock /
//           rename / duplicate / delete), its exposed controls, and
//           "Save as Motion Graphics Template".
// Also hosts the brand kit selector used for new text styles.

import { get, post, type Brand, type GraphicResult, type TemplateSummary } from "../api";
import * as actions from "../actions";
import * as gfx from "../graphics";
import { media } from "../media";
import { aspectOf, clipEnd, findClip, normalizeClip, type Clip, type Timeline } from "../model";
import * as ops from "../ops";
import { store } from "../store";
import { button, clear, h, icon, modal, promptDialog, toast } from "../ui";

const BRAND_KEY = "champ-editor-brand";
const GROUPS: Array<[string, string, (t: TemplateSummary) => boolean]> = [
  ["all", "All", () => true],
  ["lower-third", "Lower thirds", (t) => has(t, "lower third")],
  ["title", "Titles", (t) => has(t, "title")],
  ["callout", "Callouts & social", (t) => has(t, "callout") || has(t, "social")],
  ["data", "Data & stats", (t) => has(t, "stat") || has(t, "data")],
  ["cta", "CTAs & end cards", (t) => has(t, "cta") || has(t, "end card")],
  ["transition", "Transitions", (t) => has(t, "transition")],
];
const LAYER_KINDS: Array<[Parameters<typeof gfx.makeLayer>[0], string, string]> = [
  ["text", "Text", "text"],
  ["rect", "Rectangle", "grid"],
  ["ellipse", "Ellipse", "target"],
  ["line", "Line", "chevr"],
  ["triangle", "Triangle", "play"],
  ["polygon", "Polygon", "grid"],
  ["star", "Star", "sparkle"],
  ["image", "Image", "image"],
  ["color", "Background", "film"],
];

function has(t: TemplateSummary, tag: string): boolean {
  return (t.tags ?? []).some((x) => x.toLowerCase().includes(tag)) || t.name.toLowerCase().includes(tag);
}

export class GraphicsPanel {
  readonly el: HTMLElement;
  private browse: HTMLElement;
  private edit: HTMLElement;
  private grid: HTMLElement;
  private chips: HTMLElement;
  private search: HTMLInputElement;
  private brandSel: HTMLSelectElement;
  private tabBtns: HTMLButtonElement[] = [];
  private brands: Brand[] = [];
  private templates: TemplateSummary[] = [];
  private group = "all";
  private loaded = false;
  private mode: "browse" | "edit" = "browse";
  private editKey = "";

  constructor() {
    this.grid = h("div", { class: "template-grid" });
    this.chips = h("div", { class: "chip-row" });
    this.search = h("input", { class: "input", type: "search", placeholder: "Search graphics", "aria-label": "Search graphics templates" });
    this.search.addEventListener("input", () => this.renderGrid());
    this.brandSel = h("select", { class: "input", "aria-label": "Brand kit" });
    this.brandSel.addEventListener("change", () => this.setBrand(this.brandSel.value));
    this.browse = h(
      "div",
      { class: "eg-pane" },
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Brand kit (templates, titles & captions use its styles)"), this.brandSel),
      this.search,
      this.chips,
      this.grid,
    );
    this.edit = h("div", { class: "eg-pane" });
    const tab = (id: "browse" | "edit", label: string) => {
      const b = h("button", { class: `tab${id === "browse" ? " on" : ""}`, type: "button", role: "tab", onclick: () => this.setMode(id) }, label);
      this.tabBtns.push(b);
      return b;
    };
    this.el = h("div", { class: "graphics-panel eg" }, h("div", { class: "eg-tabs", role: "tablist" }, tab("browse", "Browse"), tab("edit", "Edit")), this.browse, this.edit);
    this.edit.style.display = "none";
    store.on("project", () => this.loaded && this.renderGrid());
    store.on("selection", () => {
      const g = actions.selectedGraphic();
      if (g && this.mode === "browse" && store.layer) this.setMode("edit");
      else this.renderEdit();
    });
    store.on("doc", () => this.renderEdit());
    store.on("readonly", () => this.renderEdit(true));
  }

  setMode(m: "browse" | "edit"): void {
    this.mode = m;
    this.tabBtns.forEach((b, i) => b.classList.toggle("on", (i === 0) === (m === "browse")));
    this.browse.style.display = m === "browse" ? "" : "none";
    this.edit.style.display = m === "edit" ? "" : "none";
    if (m === "edit") this.renderEdit(true);
  }

  async load(): Promise<void> {
    if (this.loaded) return;
    this.loaded = true;
    clear(this.grid);
    for (let i = 0; i < 6; i++) this.grid.append(h("div", { class: "template-card skeleton" }));
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
    await this.loadTemplates();
    this.renderEdit(true);
  }

  private async loadTemplates(): Promise<void> {
    try {
      const t = await get<{ templates: TemplateSummary[] }>("/api/templates");
      this.templates = t.templates.filter((x) => x.graphic || x.overlay || x.category === "brand-utility" || x.category === "motion-graphics");
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

  // ── Browse ──

  private renderGrid(): void {
    clear(this.chips);
    for (const [id, label, test] of GROUPS) {
      const n = this.templates.filter(test).length;
      if (id !== "all" && !n) continue;
      this.chips.append(h("button", { class: `chip${this.group === id ? " on" : ""}`, type: "button", onclick: () => ((this.group = id), this.renderGrid()) }, `${label} ${n}`));
    }
    if (!this.loaded) return;
    clear(this.grid);
    const q = this.search.value.trim().toLowerCase();
    const test = GROUPS.find((g) => g[0] === this.group)?.[2] ?? (() => true);
    const list = this.templates.filter((t) => test(t) && (!q || `${t.name} ${t.description ?? ""} ${(t.tags ?? []).join(" ")}`.toLowerCase().includes(q)));
    if (!list.length) {
      this.grid.append(h("div", { class: "empty" }, icon("grid", 24), h("p", null, "No graphics match."), h("p", { class: "muted" }, "Save your own from the Edit tab, or clear the search.")));
      return;
    }
    const aspect = aspectOf(store.doc);
    for (const t of list) {
      const brand = this.brandSel.value ? `&brand=${encodeURIComponent(this.brandSel.value)}` : "";
      const base = `/api/templates/${t.id}/preview.jpg?aspect=${encodeURIComponent(aspect)}${brand}`;
      const img = h("img", { src: base, alt: "", loading: "lazy" });
      img.addEventListener("error", () => img.replaceWith(h("div", { class: "template-fallback" }, icon("grid", 22))));
      let timer = 0;
      const card = h(
        "button",
        { class: "template-card", type: "button", "data-tip": `${t.description ?? t.name} — click to add at the playhead`, onclick: () => void this.insert(t) },
        h("div", { class: "template-thumb" }, img),
        h("span", { class: "template-name" }, t.name),
        t.origin === "team" ? h("span", { class: "badge team" }, "Team") : null,
      );
      // animated hover preview: step through the graphic's timeline
      card.addEventListener("mouseenter", () => {
        let i = 0;
        const steps = [0.08, 0.2, 0.35, 0.5, 0.7, 0.92];
        timer = window.setInterval(() => {
          img.src = `${base}&at=${steps[i++ % steps.length]}`;
        }, 260);
      });
      card.addEventListener("mouseleave", () => {
        clearInterval(timer);
        img.src = base;
      });
      this.grid.append(card);
    }
  }

  private async insert(t: TemplateSummary, at = store.playhead): Promise<void> {
    if (store.readOnly) {
      toast(store.readOnlyReason || "Read-only", "warn");
      return;
    }
    try {
      const r = await post<GraphicResult>(`/api/templates/${t.id}/graphic`, { values: {}, brand_kit_id: this.brandSel.value || null, aspect: aspectOf(store.doc), start: at });
      const clip = normalizeClip(structuredClone(r.clip));
      clip.id = `g_${t.id}_${Math.random().toString(36).slice(2, 7)}`;
      store.apply(`Insert ${t.name}`, (tl) => insertGraphic(tl, clip));
      store.select([clip.id]);
      toast(`Added ${t.name} — edit its fields in Effect Controls or Essential Graphics → Edit`, "success", 3500);
    } catch (e) {
      toast(`Couldn't insert ${t.name}: ${(e as Error).message}`, "error");
    }
  }

  // ── Edit ──

  private renderEdit(force = false): void {
    if (this.mode !== "edit") return;
    const g = actions.selectedGraphic();
    const key = JSON.stringify([g?.id, g?.graphic?.layers.map((l) => [l.id, l.name, l.type, l.enabled, l.locked, !!l.pin, l.mask]), g?.graphic?.controls.map((k) => [k.id, k.label]), store.layer?.id, store.readOnly]);
    if (!force && key === this.editKey) return;
    this.editKey = key;
    clear(this.edit);
    if (!g) {
      const sel = actions.selectedIds(false).filter((id) => ["text", "shape", "image", "color"].includes(findClip(store.doc, id)?.clip.type ?? ""));
      this.edit.append(h("div", { class: "empty" }, icon("grid", 24), h("p", null, "Select a graphic clip to edit its layers."), h("p", { class: "muted" }, "Graphics are clips that hold a stack of text, shape and image layers, each with its own animation.")));
      if (!store.readOnly)
        this.edit.append(
          h(
              "div",
              { class: "row gap wrap center" },
              button("New graphic", () => actions.newGraphic(), "btn btn-primary btn-sm", "plus", "New graphic with a text layer at the playhead"),
              sel.length ? button(`Group ${sel.length} clip${sel.length === 1 ? "" : "s"} into graphic`, () => actions.groupIntoGraphic(), "btn btn-sm", "link") : null,
            ),
      );
      return;
    }
    const ro = store.readOnly;
    const layers = g.graphic?.layers ?? [];
    const head = h(
      "div",
      { class: "eg-head" },
      h("button", { class: `linklike eg-title${store.layer ? "" : " on"}`, type: "button", onclick: () => store.selectLayer(g.id, null), "data-tip": "Select the whole graphic" }, g.name || "Graphic"),
      h("span", { class: "muted" }, `${layers.length} layer${layers.length === 1 ? "" : "s"}`),
    );
    const add = h("div", { class: "eg-add" });
    if (!ro) {
      for (const [kind, label, ic] of LAYER_KINDS) {
        add.append(
          button(label, () => {
            if (kind === "image") return void this.pickImage((id) => actions.addGraphicLayer("image", { asset_id: id }));
            actions.addGraphicLayer(kind);
          }, "btn btn-sm btn-ghost", ic, `Add ${label.toLowerCase()} layer`),
        );
      }
    }
    // layer list: top of the stack first (like Premiere)
    const list = h("div", { class: "layer-list", role: "listbox", "aria-label": "Layers" });
    [...layers].reverse().forEach((l) => list.append(this.layerRow(g, l, layers.length)));
    if (!layers.length) list.append(h("p", { class: "hint" }, "Empty graphic — add a layer above."));
    this.edit.append(head);
    if (!ro) this.edit.append(h("div", { class: "field-label" }, "New layer"), add);
    this.edit.append(h("div", { class: "field-label" }, "Layers (top first)"), list);

    // controls
    const ctl = h("div", { class: "eg-controls" });
    const controls = g.graphic?.controls ?? [];
    if (!controls.length) ctl.append(h("p", { class: "hint" }, "Select a layer, then use Effect Controls → Exposed properties to turn its text, colours or visibility into fields editors can change without opening the layers."));
    for (const k of controls) {
      const users = layers.filter((l) => gfx.boundControls(l).includes(k.id)).length;
      ctl.append(
        h(
          "div",
          { class: "control-row" },
          h("span", { class: "chip" }, k.kind),
          h("span", { class: "grow ellipsis", "data-tip": `{{${k.id}}} · used by ${users} layer${users === 1 ? "" : "s"}` }, k.label || k.id),
          ro
            ? null
            : h("button", { class: "btn icon-btn btn-ghost", type: "button", "aria-label": "Rename control", "data-tip": "Rename", onclick: async () => {
                const v = await promptDialog("Rename property", "Label shown to editors", k.label || k.id, "Rename");
                if (v) store.apply("Rename property", (tl) => gfx.updateControl(tl, g.id, k.id, (x) => (x.label = v)));
              } }, icon("text", 12)),
          ro ? null : h("button", { class: "btn icon-btn btn-ghost", type: "button", "aria-label": "Remove control", "data-tip": "Remove (keeps the current value in the layers)", onclick: () => store.apply("Remove property", (tl) => gfx.removeControl(tl, g.id, k.id)) }, icon("close", 12)),
        ),
      );
    }
    this.edit.append(h("div", { class: "field-label" }, "Master properties"), ctl);
    if (!ro) {
      this.edit.append(
        h(
          "div",
          { class: "row gap wrap" },
          button("Save as Motion Graphics Template…", () => void this.saveTemplate(g), "btn btn-sm", "save"),
          button("Ungroup", () => actions.ungroupGraphic(), "btn btn-sm btn-ghost", undefined, "Break into separate clips"),
        ),
      );
    }
  }

  private layerRow(g: Clip, l: Clip, n: number): HTMLElement {
    const ro = store.readOnly;
    const on = store.layer?.clip === g.id && store.layer.id === l.id;
    const idx = g.graphic!.layers.findIndex((x) => x.id === l.id);
    const mut = (label: string, fn: (x: Clip) => void) => store.apply(label, (tl) => gfx.updateLayer(tl, g.id, l.id, fn));
    const name = h("span", { class: "grow ellipsis layer-name" }, l.name || defaultLayerName(l));
    name.addEventListener("dblclick", async (e) => {
      e.stopPropagation();
      if (ro) return;
      const v = await promptDialog("Rename layer", "Layer name", l.name || defaultLayerName(l), "Rename");
      if (v !== null) mut("Rename layer", (x) => (x.name = v));
    });
    const row = h(
      "div",
      { class: `layer-row${on ? " on" : ""}${l.enabled ? "" : " off"}`, role: "option", "aria-selected": on ? "true" : "false", tabindex: 0, draggable: !ro, onclick: () => store.selectLayer(g.id, l.id) },
      h("button", { class: "btn icon-btn btn-ghost", type: "button", "aria-label": l.enabled ? "Hide layer" : "Show layer", "data-tip": l.enabled ? "Hide" : "Show", disabled: ro, onclick: (e: MouseEvent) => (e.stopPropagation(), mut(l.enabled ? "Hide layer" : "Show layer", (x) => (x.enabled = !x.enabled))) }, icon(l.enabled ? "eye" : "eyeoff", 13)),
      h("button", { class: "btn icon-btn btn-ghost", type: "button", "aria-label": l.locked ? "Unlock layer" : "Lock layer", "data-tip": l.locked ? "Unlock (monitor)" : "Lock on the monitor", disabled: ro, onclick: (e: MouseEvent) => (e.stopPropagation(), mut(l.locked ? "Unlock layer" : "Lock layer", (x) => (x.locked = !x.locked))) }, icon(l.locked ? "lock" : "unlock", 13)),
      h("span", { class: `type-dot t-${l.type}` }),
      name,
      l.pin ? h("span", { class: "chip mini", "data-tip": `Pinned to ${l.pin.to}` }, "pin") : null,
      l.mask ? h("span", { class: "chip mini", "data-tip": `Mask: ${l.mask}` }, "mask") : null,
      Object.keys(l.bind).length ? h("span", { class: "chip mini", "data-tip": `Driven by ${gfx.boundControls(l).join(", ")}` }, "ƒ") : null,
      ro
        ? null
        : h(
            "span",
            { class: "layer-tools" },
            h("button", { class: "btn icon-btn btn-ghost", type: "button", "aria-label": "Move layer up", "data-tip": "Bring forward", disabled: idx >= n - 1, onclick: (e: MouseEvent) => (e.stopPropagation(), store.apply("Bring layer forward", (tl) => gfx.moveLayer(tl, g.id, l.id, 1))) }, icon("chevl", 11)),
            h("button", { class: "btn icon-btn btn-ghost", type: "button", "aria-label": "Move layer down", "data-tip": "Send backward", disabled: idx <= 0, onclick: (e: MouseEvent) => (e.stopPropagation(), store.apply("Send layer backward", (tl) => gfx.moveLayer(tl, g.id, l.id, -1))) }, icon("chevr", 11)),
            h("button", { class: "btn icon-btn btn-ghost", type: "button", "aria-label": "Duplicate layer", "data-tip": "Duplicate", onclick: (e: MouseEvent) => (e.stopPropagation(), store.apply("Duplicate layer", (tl) => gfx.duplicateLayer(tl, g.id, l.id))) }, icon("plus", 11)),
            h("button", { class: "btn icon-btn btn-ghost", type: "button", "aria-label": "Delete layer", "data-tip": "Delete", onclick: (e: MouseEvent) => (e.stopPropagation(), store.apply("Delete layer", (tl) => gfx.removeLayer(tl, g.id, l.id))) }, icon("close", 11)),
          ),
    );
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter") store.selectLayer(g.id, l.id);
      if ((e.key === "Delete" || e.key === "Backspace") && !ro) {
        e.preventDefault();
        e.stopPropagation();
        store.apply("Delete layer", (tl) => gfx.removeLayer(tl, g.id, l.id));
      }
    });
    // drag to reorder (list is top-first, stack is bottom-first)
    row.addEventListener("dragstart", (e) => e.dataTransfer?.setData("application/x-champ-layer", l.id));
    row.addEventListener("dragover", (e) => {
      if (e.dataTransfer?.types.includes("application/x-champ-layer")) {
        e.preventDefault();
        row.classList.add("drop");
      }
    });
    row.addEventListener("dragleave", () => row.classList.remove("drop"));
    row.addEventListener("drop", (e) => {
      row.classList.remove("drop");
      const id = e.dataTransfer?.getData("application/x-champ-layer");
      if (id && id !== l.id) store.apply("Reorder layers", (tl) => gfx.reorderLayer(tl, g.id, id, idx));
    });
    return row;
  }

  private pickImage(done: (assetId: string) => void): void {
    const imgs = [...media.assets.values()].filter((a) => a.kind === "image");
    if (!imgs.length) {
      toast("Import an image into the Project panel first", "info");
      return;
    }
    const m = modal("Add image layer", { wide: true });
    const grid = h("div", { class: "image-pick" });
    for (const a of imgs) {
      grid.append(h("button", { class: "image-pick-item", type: "button", onclick: () => (m.close(), done(a.id)) }, h("img", { src: media.fileUrl(a.id), alt: "", loading: "lazy" }), h("span", { class: "ellipsis" }, String(a.params?.filename ?? a.prompt ?? a.id))));
    }
    m.body.append(grid);
    m.footer.append(button("Cancel", () => m.close()));
  }

  private async saveTemplate(g: Clip): Promise<void> {
    const m = modal("Save as Motion Graphics Template");
    const name = h("input", { class: "input", type: "text", value: g.name && g.name !== "Graphic" ? g.name : "", placeholder: "e.g. Speaker lower third", "aria-label": "Template name" });
    const desc = h("input", { class: "input", type: "text", placeholder: "What it's for (optional)", "aria-label": "Description" });
    m.body.append(
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Name"), name),
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Description"), desc),
      h("p", { class: "hint" }, `Saved to your team's library with ${g.graphic?.controls.length ?? 0} editable field(s). Everyone on this server can use it from Browse.`),
    );
    m.footer.append(
      button("Cancel", () => m.close()),
      button("Save template", async () => {
        if (name.value.trim().length < 3) {
          toast("Give the template a name (3+ characters)", "warn");
          return;
        }
        try {
          await post("/api/templates/from-graphic", { clip: g, name: name.value.trim(), description: desc.value.trim(), aspect: aspectOf(store.doc) });
          m.close();
          toast(`Saved “${name.value.trim()}” to the team library`, "success");
          await this.loadTemplates();
        } catch (e) {
          toast(`Couldn't save: ${(e as Error).message}`, "error");
        }
      }, "btn btn-primary"),
    );
    name.focus();
  }
}

function defaultLayerName(l: Clip): string {
  if (l.type === "text") return l.text.split("\n")[0]?.slice(0, 32) || "Text";
  if (l.type === "shape") return l.shape.kind[0]!.toUpperCase() + l.shape.kind.slice(1);
  return l.type[0]!.toUpperCase() + l.type.slice(1);
}

/** Place a graphic clip on a free video track above existing picture, adding a track if needed. */
export function insertGraphic(tl: Timeline, clip: Clip): Timeline {
  const s = clip.start;
  const e = clipEnd(clip);
  let out = tl;
  const vids = out.tracks.filter((t) => t.kind === "video");
  let topUsed = -1;
  vids.forEach((t, i) => {
    if (t.clips.some((c) => c.start < e && clipEnd(c) > s)) topUsed = i;
  });
  let tid: string | null = null;
  for (let i = topUsed + 1; i < vids.length; i++) {
    const t = vids[i]!;
    if (!t.locked && t.clips.every((c) => clipEnd(c) <= s || c.start >= e)) {
      tid = t.id;
      break;
    }
  }
  if (!tid) {
    const a = ops.addTrack(out, "video");
    out = a.tl;
    tid = a.id;
  }
  return ops.overwrite(out, [{ trackId: tid, clip }]);
}

function safeGet(k: string): string | null {
  try {
    return localStorage.getItem(k);
  } catch {
    return null;
  }
}
