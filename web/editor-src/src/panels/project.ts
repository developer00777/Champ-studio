// Project panel: media bins (All / Video / Audio / Images), search, upload
// (button + drag-drop), thumbnails; double-click opens the Source monitor.

import { ApiError, type Asset } from "../api";
import { media } from "../media";
import { store } from "../store";
import { button, clear, h, icon, toast } from "../ui";

type Bin = "all" | "video" | "audio" | "image";

export class ProjectPanel {
  readonly el: HTMLElement;
  private list: HTMLElement;
  private bin: Bin = "all";
  private query = "";
  private loading = true;
  private error = "";
  private observer: IntersectionObserver;
  private uploads = new Map<string, { name: string; pct: number }>();
  private uploadsEl: HTMLElement;
  selected = new Set<string>();
  onOpen: (a: Asset) => void = () => {};

  constructor() {
    this.list = h("div", { class: "asset-grid", role: "listbox", "aria-label": "Project media", "aria-multiselectable": "true" });
    this.uploadsEl = h("div", { class: "uploads" });
    const fileInput = h("input", { type: "file", multiple: true, accept: "video/*,audio/*,image/*,.cube,.mov,.mkv", style: "display:none" });
    fileInput.addEventListener("change", () => {
      if (fileInput.files) void this.upload([...fileInput.files]);
      fileInput.value = "";
    });
    const bins = h("div", { class: "seg", role: "tablist", "aria-label": "Bins" });
    for (const [id, label] of [
      ["all", "All"],
      ["video", "Video"],
      ["audio", "Audio"],
      ["image", "Images"],
    ] as const) {
      bins.append(
        h("button", {
          class: `seg-btn${this.bin === id ? " on" : ""}`,
          type: "button",
          role: "tab",
          "aria-selected": String(this.bin === id),
          onclick: (e: MouseEvent) => {
            this.bin = id;
            bins.querySelectorAll(".seg-btn").forEach((b) => {
              b.classList.toggle("on", b === e.currentTarget);
              b.setAttribute("aria-selected", String(b === e.currentTarget));
            });
            this.render();
          },
        }, label),
      );
    }
    const search = h("input", { class: "input search", type: "search", placeholder: "Search media", "aria-label": "Search media" });
    search.addEventListener("input", () => {
      this.query = search.value.toLowerCase();
      this.render();
    });
    this.el = h(
      "div",
      { class: "project-panel" },
      h("div", { class: "panel-toolbar" }, h("div", { class: "search-wrap" }, icon("search", 14), search), button("Import", () => fileInput.click(), "btn btn-sm", "upload", "Upload video, audio, images or .cube LUTs"), fileInput),
      bins,
      this.uploadsEl,
      this.list,
    );
    this.el.addEventListener("dragover", (e) => {
      if (e.dataTransfer?.types.includes("Files")) {
        e.preventDefault();
        this.el.classList.add("drop");
      }
    });
    this.el.addEventListener("dragleave", (e) => {
      if (e.target === this.el || !this.el.contains(e.relatedTarget as Node)) this.el.classList.remove("drop");
    });
    this.el.addEventListener("drop", (e) => {
      e.preventDefault();
      this.el.classList.remove("drop");
      const files = [...(e.dataTransfer?.files ?? [])];
      if (files.length) void this.upload(files);
    });
    this.observer = new IntersectionObserver((entries) => {
      for (const en of entries) {
        if (!en.isIntersecting) continue;
        const id = (en.target as HTMLElement).dataset.id!;
        const a = media.assets.get(id);
        if (a?.kind === "video") void media.ensure(id).catch(() => undefined);
        this.observer.unobserve(en.target);
      }
    });
    media.on("assets", () => this.render());
    media.on("media", () => this.refreshThumbs());
    this.render();
  }

  async load(): Promise<void> {
    this.loading = true;
    this.render();
    try {
      await media.loadAssets();
      this.error = "";
    } catch (e) {
      this.error = (e as Error).message;
    }
    this.loading = false;
    this.render();
  }

  private filtered(): Asset[] {
    const all = [...media.assets.values()].filter((a) => ["video", "audio", "image", "vector"].includes(a.kind) || /\.cube$/i.test(name(a)));
    return all
      .filter((a) => this.bin === "all" || a.kind === this.bin || (this.bin === "image" && a.kind === "vector"))
      .filter((a) => !this.query || name(a).toLowerCase().includes(this.query) || (a.prompt ?? "").toLowerCase().includes(this.query))
      .sort((a, b) => String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")));
  }

  private render(): void {
    clear(this.list);
    if (this.loading && !media.assets.size) {
      for (let i = 0; i < 6; i++) this.list.append(h("div", { class: "asset skeleton" }));
      return;
    }
    if (this.error) {
      this.list.append(h("div", { class: "empty" }, h("p", null, "Couldn't load media."), h("p", { class: "muted" }, this.error), button("Retry", () => void this.load(), "btn btn-sm")));
      return;
    }
    const items = this.filtered();
    if (!items.length) {
      this.list.append(
        h(
          "div",
          { class: "empty" },
          icon("film", 28),
          h("p", null, this.query ? "No media matches your search." : "No media yet."),
          h("p", { class: "muted" }, "Drag files here or click Import. Video gets proxies, thumbnails and waveforms automatically."),
        ),
      );
      return;
    }
    for (const a of items) this.list.append(this.card(a));
  }

  private card(a: Asset): HTMLElement {
    const thumb = h("div", { class: "asset-thumb" });
    this.fillThumb(thumb, a);
    const el = h(
      "div",
      {
        class: `asset${this.selected.has(a.id) ? " selected" : ""}`,
        role: "option",
        tabindex: 0,
        draggable: "true",
        "data-id": a.id,
        "aria-selected": String(this.selected.has(a.id)),
        title: `${name(a)}\nDouble-click to open in the Source monitor`,
      },
      thumb,
      h("div", { class: "asset-name" }, kindIcon(a.kind), h("span", null, name(a))),
    );
    el.addEventListener("click", (e) => {
      if (!(e.shiftKey || e.ctrlKey || e.metaKey)) this.selected.clear();
      if (this.selected.has(a.id)) this.selected.delete(a.id);
      else this.selected.add(a.id);
      this.list.querySelectorAll<HTMLElement>(".asset").forEach((n) => {
        const on = this.selected.has(n.dataset.id!);
        n.classList.toggle("selected", on);
        n.setAttribute("aria-selected", String(on));
      });
    });
    el.addEventListener("dblclick", () => this.onOpen(a));
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter") this.onOpen(a);
    });
    el.addEventListener("dragstart", (e) => {
      e.dataTransfer?.setData("application/x-champ-asset", a.id);
      e.dataTransfer?.setData("text/plain", a.id);
    });
    if (a.kind === "video") this.observer.observe(el);
    return el;
  }

  private fillThumb(thumb: HTMLElement, a: Asset): void {
    clear(thumb);
    if (a.kind === "image" || a.kind === "vector") {
      thumb.append(h("img", { src: media.fileUrl(a.id), alt: "", loading: "lazy", decoding: "async" }));
    } else if (a.kind === "video") {
      const st = media.status.get(a.id);
      if (st?.thumbs && st.thumbs_meta) {
        const m = st.thumbs_meta;
        const mid = Math.min(m.tiles - 1, Math.floor(m.tiles / 3));
        thumb.style.backgroundImage = `url(${st.thumbs})`;
        thumb.style.backgroundSize = `${m.tiles * 100}% 100%`;
        thumb.style.backgroundPosition = `${m.tiles > 1 ? (mid / (m.tiles - 1)) * 100 : 0}% 0`;
      } else thumb.append(icon("film", 22));
      if (media.building.has(a.id)) thumb.append(h("span", { class: "badge badge-mini" }, "proxy…"));
      const d = media.duration(a.id);
      if (d) thumb.append(h("span", { class: "dur mono" }, fmtDur(d)));
    } else if (a.kind === "audio") {
      thumb.append(icon("music", 22));
      const d = media.duration(a.id);
      if (d) thumb.append(h("span", { class: "dur mono" }, fmtDur(d)));
    } else thumb.append(icon("grid", 20));
  }

  private refreshThumbs(): void {
    this.list.querySelectorAll<HTMLElement>(".asset").forEach((n) => {
      const a = media.assets.get(n.dataset.id!);
      const t = n.querySelector<HTMLElement>(".asset-thumb");
      if (a && t && a.kind !== "image") this.fillThumb(t, a);
    });
  }

  async upload(files: File[]): Promise<void> {
    if (store.readOnly && store.me.role === "viewer") {
      toast("Viewers can't upload media", "warn");
      return;
    }
    await Promise.all(files.map((f) => this.uploadOne(f)));
  }

  private renderUploads(): void {
    clear(this.uploadsEl);
    for (const u of this.uploads.values()) {
      this.uploadsEl.append(h("div", { class: "upload-row" }, h("span", { class: "grow ellipsis" }, u.name), h("span", { class: "mono" }, `${Math.round(u.pct * 100)}%`), h("div", { class: "upload-bar", style: `width:${u.pct * 100}%` })));
    }
  }

  private uploadOne(file: File): Promise<void> {
    const key = `${file.name}-${Math.random()}`;
    this.uploads.set(key, { name: file.name, pct: 0 });
    this.renderUploads();
    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      const fd = new FormData();
      fd.append("file", file);
      xhr.open("POST", "/api/assets/upload");
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          this.uploads.set(key, { name: file.name, pct: e.loaded / e.total });
          this.renderUploads();
        }
      };
      xhr.onload = () => {
        this.uploads.delete(key);
        this.renderUploads();
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            const a = JSON.parse(xhr.responseText) as Asset;
            media.addAsset(a);
            if (a.kind === "video" || a.kind === "audio") void media.ensure(a.id).catch(() => undefined);
            toast(`Imported ${file.name}`, "success", 2500);
          } catch {
            toast(`Imported ${file.name}`, "success", 2500);
            void media.loadAssets();
          }
        } else {
          let detail = xhr.statusText;
          try {
            detail = JSON.parse(xhr.responseText).detail ?? detail;
          } catch {
            /* ignore */
          }
          toast(`Upload failed (${file.name}): ${new ApiError(xhr.status, detail).message}`, "error", 7000);
        }
        resolve();
      };
      xhr.onerror = () => {
        this.uploads.delete(key);
        this.renderUploads();
        toast(`Upload failed (${file.name}): network error`, "error");
        resolve();
      };
      xhr.send(fd);
    });
  }
}

export function name(a: Asset): string {
  return String(a.params?.filename || a.prompt || a.filename || a.id);
}

function kindIcon(kind: string): SVGSVGElement {
  return icon(kind === "video" ? "film" : kind === "audio" ? "music" : kind === "image" || kind === "vector" ? "image" : "grid", 12);
}

function fmtDur(s: number): string {
  const m = Math.floor(s / 60);
  const ss = Math.floor(s % 60);
  return `${m}:${String(ss).padStart(2, "0")}`;
}
