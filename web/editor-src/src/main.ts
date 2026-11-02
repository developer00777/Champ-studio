// Champ Studio Editor — app shell: loads the project, builds the panels and
// wires state, autosave, locks, read-only roles and shortcuts.

import "./styles.css";
import * as actions from "./actions";
import { ApiError, get, post, type Me, type Project } from "./api";
import { autosave } from "./autosave";
import { media } from "./media";
import { ASPECTS, makeClip, newId, type Clip } from "./model";
import * as ops from "./ops";
import { AiDrawer } from "./panels/ai";
import { CommentsPanel } from "./panels/comments";
import { openExport, openShortcuts, openVersions } from "./panels/dialogs";
import { GraphicsPanel } from "./panels/graphics";
import { Inspector } from "./panels/inspector";
import { ProgramMonitor, SourceMonitor } from "./panels/monitors";
import { name as assetName, ProjectPanel } from "./panels/project";
import { installShortcuts } from "./shortcuts";
import { store } from "./store";
import { TimelineView } from "./timeline-view";
import { button, clear, fmtDate, h, icon, iconButton, installTooltips, promptDialog, toast } from "./ui";

const root = document.getElementById("editor-root")!;
let previewRef: unknown = null;
let tvRef: unknown = null;

async function boot(): Promise<void> {
  const params = new URLSearchParams(location.search);
  const pid = params.get("project");
  root.replaceChildren(h("div", { class: "boot" }, h("div", { class: "spinner" }), h("p", null, "Loading Champ Studio Editor…")));
  try {
    store.me = await get<Me>("/api/me");
  } catch {
    /* local engines without auth */
  }
  if (!pid) return picker();
  let project: Project;
  try {
    project = await get<Project>(`/api/projects/${encodeURIComponent(pid)}`);
  } catch (e) {
    const msg = e instanceof ApiError && e.status === 404 ? "This project doesn't exist (or was deleted)." : `Couldn't load the project: ${(e as Error).message}`;
    root.replaceChildren(h("div", { class: "boot" }, h("h1", null, "Can't open project"), h("p", { class: "muted" }, msg), button("Choose another project", () => (location.search = ""), "btn btn-primary")));
    return;
  }
  store.project = project;
  store.load(project.data);
  const role = store.me.role;
  if (role === "viewer" || role === "reviewer") store.setReadOnly(true, `Read-only: your role is ${role}. You can watch and comment.`);
  else if (project.locked_by && project.locked_by !== store.me.id) store.setReadOnly(true, `Read-only: locked by ${project.locked_by}.`);
  document.title = `${project.name} — Champ Studio Editor`;
  await Promise.race([media.loadFonts(), new Promise((r) => setTimeout(r, 2500))]);
  build();
  void media.loadAssets().catch(() => undefined);
  // warm media status for every clip on the timeline (triggers proxy builds)
  const ids = new Set<string>();
  for (const t of store.doc.tracks) for (const c of t.clips) if (c.asset_id) ids.add(c.asset_id);
  for (const id of ids) void media.ensure(id).catch(() => undefined);
}

// ── project picker (no ?project=) ────────────────────────────────────

async function picker(): Promise<void> {
  const list = h("div", { class: "picker-list" }, h("p", { class: "muted" }, "Loading projects…"));
  const nameIn = h("input", { class: "input", type: "text", value: "Untitled sequence", "aria-label": "Name" });
  const aspect = h("select", { class: "input", "aria-label": "Aspect" }, Object.keys(ASPECTS).map((a) => h("option", { value: a }, a)));
  const fps = h("select", { class: "input", "aria-label": "Frame rate" }, ["30", "25", "24", "60"].map((f) => h("option", { value: f }, `${f} fps`)));
  root.replaceChildren(
    h(
      "div",
      { class: "picker" },
      h("h1", null, h("span", { class: "logo-mark" }), "Champ Studio Editor"),
      h(
        "div",
        { class: "picker-new" },
        h("h2", null, "New sequence"),
        h("div", { class: "row gap wrap" }, nameIn, aspect, fps, button("Create", async () => {
          try {
            const p = await post<Project>("/api/projects", { name: nameIn.value || "Untitled sequence", aspect: aspect.value, fps: Number(fps.value) });
            location.search = `?project=${encodeURIComponent(p.id)}`;
          } catch (e) {
            toast(`Couldn't create: ${(e as Error).message}`, "error");
          }
        }, "btn btn-primary")),
      ),
      h("h2", null, "Recent sequences"),
      list,
    ),
  );
  try {
    const r = await get<{ projects: Project[] }>("/api/projects?kind=edit");
    clear(list);
    if (!r.projects.length) list.append(h("p", { class: "muted" }, "No sequences yet — create one above."));
    for (const p of r.projects) list.append(h("a", { class: "picker-item", href: `?project=${encodeURIComponent(p.id)}` }, h("strong", null, p.name), h("span", { class: "muted" }, ` · v${p.version} · ${fmtDate(p.updated_at)}`)));
  } catch (e) {
    clear(list);
    list.append(h("p", { class: "error" }, `Couldn't list projects: ${(e as Error).message}`));
  }
}

// ── main layout ──────────────────────────────────────────────────────

function build(): void {
  const project = new ProjectPanel();
  const graphics = new GraphicsPanel();
  const comments = new CommentsPanel();
  const source = new SourceMonitor();
  const program = new ProgramMonitor();
  previewRef = program.preview;
  const inspector = new Inspector();
  const ai = new AiDrawer();
  ai.getSelectedAssets = () => [...project.selected];
  project.onOpen = (a) => source.open(a);

  // left panel tabs
  const leftBody = h("div", { class: "panel-body" }, project.el);
  const commentCount = h("span", { class: "count", style: "display:none" });
  comments.onCountChange = (n) => {
    commentCount.textContent = String(n);
    commentCount.style.display = n ? "" : "none";
  };
  const leftTabs = tabs(
    [
      ["project", "Project", null],
      ["graphics", "Graphics", null],
      ["comments", "Comments", commentCount],
    ],
    (id) => {
      leftBody.replaceChildren(id === "project" ? project.el : id === "graphics" ? graphics.el : comments.el);
      if (id === "graphics") void graphics.load();
    },
  );
  const left = h("section", { class: "panel panel-left", "aria-label": "Project" }, leftTabs, leftBody);
  const right = h("section", { class: "panel panel-right", "aria-label": "Effect controls" }, h("div", { class: "panel-head" }, h("h2", null, "Effect Controls")), h("div", { class: "panel-body scroll" }, inspector.el));

  // timeline
  const tlHost = h("div", { class: "timeline-host" });
  const timelineView = new TimelineView(tlHost, program.preview);
  tvRef = timelineView;
  comments.attach(timelineView);
  const toolbar = timelineToolbar(timelineView);
  const tlPanel = h("section", { class: "panel timeline-panel", "aria-label": "Timeline" }, toolbar, tlHost);
  const splitter = h("div", { class: "splitter", role: "separator", "aria-orientation": "horizontal", "aria-label": "Resize timeline", tabindex: 0 });

  // drag media from the Project panel onto the timeline (overwrite edit)
  tlHost.addEventListener("dragover", (e) => {
    if (e.dataTransfer?.types.includes("application/x-champ-asset")) e.preventDefault();
  });
  tlHost.addEventListener("drop", (e) => {
    const id = e.dataTransfer?.getData("application/x-champ-asset");
    if (!id) return;
    e.preventDefault();
    const loc = timelineView.locate(e.clientX, e.clientY);
    if (loc) void dropAsset(id, loc.f, loc.track?.id ?? null);
  });

  const banner = h("div", { class: "readonly-banner", role: "status" });
  const syncBanner = () => {
    banner.textContent = store.readOnly ? store.readOnlyReason || "Read-only" : "";
    banner.style.display = store.readOnly ? "" : "none";
    document.body.classList.toggle("readonly", store.readOnly);
  };
  store.on("readonly", syncBanner);
  syncBanner();

  const app = h(
    "div",
    { class: "app" },
    topbar(ai),
    banner,
    h("main", { class: "workspace" }, left, source.el, program.el, right),
    splitter,
    tlPanel,
    ai.el,
  );
  root.replaceChildren(app);
  program.fit();

  // splitter drag
  const savedH = Number(safeGet("champ-editor-tl-h"));
  if (savedH > 120) app.style.setProperty("--tl-h", `${savedH}px`);
  splitter.addEventListener("pointerdown", (e) => {
    splitter.setPointerCapture(e.pointerId);
    const move = (ev: PointerEvent) => {
      const hgt = Math.max(160, Math.min(window.innerHeight - 260, window.innerHeight - ev.clientY));
      app.style.setProperty("--tl-h", `${hgt}px`);
    };
    const up = () => {
      splitter.removeEventListener("pointermove", move);
      splitter.removeEventListener("pointerup", up);
      try {
        localStorage.setItem("champ-editor-tl-h", app.style.getPropertyValue("--tl-h").replace("px", ""));
      } catch {
        /* storage blocked */
      }
    };
    splitter.addEventListener("pointermove", move);
    splitter.addEventListener("pointerup", up);
  });

  installShortcuts({
    preview: program.preview,
    source,
    timeline: timelineView,
    toggleAi: () => ai.toggle(),
    closeDrawers: () => {
      if (ai.open) {
        ai.toggle(false);
        return true;
      }
      return false;
    },
  });
  autosave.init();
  if (store.readOnly) autosave.markClean();
  void project.load();
  void comments.load();
  void graphics.loadBrandOnly();
  requestAnimationFrame(() => timelineView.zoomFit());
}

function tabs(items: Array<[string, string, HTMLElement | null]>, onSelect: (id: string) => void): HTMLElement {
  const el = h("div", { class: "panel-tabs", role: "tablist" });
  for (const [id, label, extra] of items) {
    el.append(
      h("button", {
        class: `tab${id === items[0]![0] ? " on" : ""}`,
        type: "button",
        role: "tab",
        "data-id": id,
        onclick: () => {
          el.querySelectorAll(".tab").forEach((b) => b.classList.toggle("on", (b as HTMLElement).dataset.id === id));
          onSelect(id);
        },
      }, label, extra),
    );
  }
  return el;
}

function topbar(ai: AiDrawer): HTMLElement {
  const p = store.project!;
  const nameIn = h("input", { class: "project-name", type: "text", value: p.name, "aria-label": "Project name", disabled: store.readOnly });
  nameIn.addEventListener("change", () => {
    const v = nameIn.value.trim();
    if (!v) return;
    autosave.pendingName = v;
    document.title = `${v} — Champ Studio Editor`;
    void autosave.flush();
  });
  nameIn.addEventListener("keydown", (e) => {
    if (e.key === "Enter") nameIn.blur();
    e.stopPropagation();
  });
  const status = h("button", { class: "save-status", type: "button", "aria-live": "polite" });
  status.addEventListener("click", () => {
    if (autosave.state.kind === "conflict" || autosave.state.kind === "offline" || autosave.state.kind === "error") void autosave.flush();
  });
  const undoBtn = iconButton("undo", "Undo", () => store.undo(), "Ctrl+Z");
  const redoBtn = iconButton("redo", "Redo", () => store.redo(), "Ctrl+Shift+Z");
  const lockBtn = h("button", { class: "btn btn-ghost btn-sm lock-btn", type: "button" });
  const syncStatus = () => {
    const s = autosave.state;
    const text: Record<string, string> = {
      saved: `Saved v${"version" in s ? s.version : store.project?.version ?? ""}`,
      dirty: "Unsaved changes",
      saving: "Saving…",
      conflict: "Conflict — click to resolve",
      offline: "Offline — retrying",
      locked: "Locked — not saved",
      error: "Save failed — retry",
      readonly: "Read-only",
    };
    status.textContent = text[s.kind] ?? s.kind;
    status.className = `save-status s-${s.kind}`;
    status.setAttribute("data-tip", s.kind === "error" ? s.message : s.kind === "locked" ? s.by : "Autosaves 2 s after each change");
  };
  const syncHistory = () => {
    undoBtn.disabled = !store.canUndo();
    redoBtn.disabled = !store.canRedo();
    undoBtn.setAttribute("data-tip", store.canUndo() ? `Undo ${store.undoLabel()} (Ctrl+Z)` : "Nothing to undo");
    redoBtn.setAttribute("data-tip", store.canRedo() ? `Redo ${store.redoLabel()} (Ctrl+Shift+Z)` : "Nothing to redo");
  };
  const syncLock = () => {
    const by = store.project?.locked_by;
    const mine = by && by === store.me.id;
    lockBtn.replaceChildren(icon(by ? "lock" : "unlock", 14), h("span", null, by ? (mine ? "Locked by you" : `Locked: ${by}`) : "Unlocked"));
    lockBtn.setAttribute("data-tip", by ? (mine ? "Release the edit lock" : "Someone else is editing") : "Take the edit lock so teammates can't overwrite you");
    lockBtn.classList.toggle("on", !!by);
    lockBtn.disabled = store.me.role === "viewer" || store.me.role === "reviewer";
  };
  lockBtn.addEventListener("click", async () => {
    const pr = store.project!;
    const mine = pr.locked_by === store.me.id;
    try {
      await autosave.flush();
      const r = await post<Project>(`/api/projects/${pr.id}/${pr.locked_by ? "unlock" : "lock"}`);
      store.project = { ...pr, locked_by: r.locked_by, version: r.version };
      if (!r.locked_by || r.locked_by === store.me.id) {
        if (store.readOnly && store.me.role !== "viewer" && store.me.role !== "reviewer") {
          store.setReadOnly(false);
          await autosave.reload();
        }
      }
      toast(mine ? "Edit lock released" : "You hold the edit lock", "success", 2000);
    } catch (e) {
      if (e instanceof ApiError && e.status === 423) toast(e.detail, "warn", 6000);
      else toast(`Lock failed: ${(e as Error).message}`, "error");
    }
    store.emit("project");
  });
  store.on("save", syncStatus);
  store.on("history", syncHistory);
  store.on("project", () => {
    syncLock();
    syncStatus();
  });
  store.on("readonly", () => {
    nameIn.disabled = store.readOnly;
    syncHistory();
  });
  syncStatus();
  syncHistory();
  syncLock();
  return h(
    "header",
    { class: "topbar" },
    h("a", { class: "brand", href: "/", "data-tip": "Back to Champ Studio" }, h("span", { class: "logo-mark" }), h("span", { class: "brand-name" }, "Champ Studio")),
    h("span", { class: "sep" }),
    nameIn,
    status,
    h("span", { class: "grow" }),
    h("div", { class: "group" }, undoBtn, redoBtn),
    lockBtn,
    button("Versions", () => void openVersions(), "btn btn-ghost btn-sm", "history", "Named versions & restore"),
    iconButton("keyboard", "Keyboard shortcuts", () => openShortcuts(), "?", "btn icon-btn btn-ghost"),
    button("AI", () => ai.toggle(), "btn btn-sm btn-ai", "sparkle", "AI editing tools (Shift+A)"),
    button("Export", () => void openExport(), "btn btn-primary btn-sm", "export", "Render, captions & interchange"),
    h("span", { class: "role-badge", "data-tip": store.me.email }, store.me.role),
  );
}

function timelineToolbar(tv: TimelineView): HTMLElement {
  const selBtn = iconButton("cursor", "Selection tool", () => store.setTool("select"), "V", "btn icon-btn tool");
  const razorBtn = iconButton("razor", "Razor tool", () => store.setTool("razor"), "C", "btn icon-btn tool");
  const snapBtn = iconButton("magnet", "Snapping", () => {
    store.snapping = !store.snapping;
    store.emit("tool");
  }, "S", "btn icon-btn tool");
  const linkBtn = iconButton("link", "Linked selection", () => {
    store.linkedSelection = !store.linkedSelection;
    store.emit("tool");
  }, "Alt-click selects one side", "btn icon-btn tool");
  const sync = () => {
    selBtn.classList.toggle("on", store.tool === "select");
    razorBtn.classList.toggle("on", store.tool === "razor");
    snapBtn.classList.toggle("on", store.snapping);
    linkBtn.classList.toggle("on", store.linkedSelection);
    for (const b of [selBtn, razorBtn, snapBtn, linkBtn]) b.setAttribute("aria-pressed", String(b.classList.contains("on")));
  };
  store.on("tool", sync);
  sync();
  const editBtns = [
    iconButton("razor", "Add edit at playhead", () => actions.splitAtPlayhead(), "Ctrl+K", "btn icon-btn btn-ghost"),
    iconButton("text", "Add title", () => actions.addTitle(), "T", "btn icon-btn btn-ghost"),
    iconButton("image", "Add color matte", () => actions.addColorMatte(), undefined, "btn icon-btn btn-ghost"),
    iconButton("sparkle", "Add adjustment layer", () => actions.addAdjustmentLayer(), undefined, "btn icon-btn btn-ghost"),
    iconButton("marker", "Add marker", () => actions.addMarker(), "M", "btn icon-btn btn-ghost"),
  ];
  const addTrackBtn = button("Track", async () => {
    const v = await promptDialog("Add tracks", "Type “v” for video or “a” for audio, optionally with a count (e.g. “a2”)", "v", "Add");
    if (!v) return;
    const kind = v.trim().toLowerCase().startsWith("a") ? "audio" : "video";
    const n = Math.max(1, Math.min(8, parseInt(v.replace(/\D/g, ""), 10) || 1));
    for (let i = 0; i < n; i++) actions.addTrack(kind);
  }, "btn btn-ghost btn-sm", "plus", "Add video or audio tracks");
  return h(
    "div",
    { class: "timeline-toolbar", role: "toolbar", "aria-label": "Timeline tools" },
    h("div", { class: "group" }, selBtn, razorBtn),
    h("div", { class: "group" }, snapBtn, linkBtn),
    h("div", { class: "group" }, ...editBtns, addTrackBtn),
    h("span", { class: "grow" }),
    h("div", { class: "group" }, iconButton("chevl", "Zoom out", () => tv.zoomOut(), "-", "btn icon-btn btn-ghost"), button("Fit", () => tv.zoomFit(), "btn btn-ghost btn-sm", undefined, "Zoom to fit (\\)"), iconButton("chevr", "Zoom in", () => tv.zoomIn(), "=", "btn icon-btn btn-ghost")),
  );
}

async function dropAsset(assetId: string, f: number, trackId: string | null): Promise<void> {
  if (store.readOnly) {
    toast(store.readOnlyReason || "Read-only", "warn");
    return;
  }
  const a = media.assets.get(assetId);
  if (!a || a.kind === "vector") return;
  const fps = store.fps;
  const dur = a.kind === "image" ? fps * 5 : Math.max(1, Math.floor(((await media.durationAsync(assetId)) ?? 5) * fps));
  const tl = store.doc;
  const dropTrack = trackId ? tl.tracks.find((t) => t.id === trackId) : undefined;
  const wantKind = a.kind === "audio" ? "audio" : "video";
  const vTrack = wantKind === "video" ? (dropTrack?.kind === "video" && !dropTrack.locked ? dropTrack : tl.tracks.find((t) => t.kind === "video" && !t.locked)) : undefined;
  const aTrack = dropTrack?.kind === "audio" && !dropTrack.locked ? dropTrack : tl.tracks.find((t) => t.kind === "audio" && !t.locked && (!vTrack || store.targets.has(t.id))) ?? tl.tracks.find((t) => t.kind === "audio" && !t.locked);
  const placements: Array<{ trackId: string; clip: Clip }> = [];
  const link = newId("lk");
  const hasAudio = a.kind === "audio" || (a.kind === "video" && media.status.get(assetId)?.probe?.has_audio !== false);
  const nm = assetName(a);
  if (vTrack) placements.push({ trackId: vTrack.id, clip: makeClip({ type: a.kind === "image" ? "image" : "video", asset_id: assetId, name: nm, start: f, duration: dur, linked: a.kind === "video" && hasAudio && aTrack ? link : null }) });
  if (aTrack && hasAudio && a.kind !== "image") placements.push({ trackId: aTrack.id, clip: makeClip({ type: "audio", asset_id: assetId, name: nm, start: f, duration: dur, linked: vTrack && a.kind === "video" ? link : null, role: a.kind === "audio" ? "music" : "dialogue" }) });
  if (!placements.length) return;
  store.apply("Overwrite edit", (t) => ops.overwrite(t, placements));
  store.select(placements.map((p) => p.clip.id));
  void media.ensure(assetId).catch(() => undefined);
}

function safeGet(k: string): string | null {
  try {
    return localStorage.getItem(k);
  } catch {
    return null;
  }
}

window.addEventListener("unhandledrejection", (e) => {
  const err = e.reason as Error;
  if (err?.name === "AbortError") {
    e.preventDefault();
    return;
  }
  e.preventDefault();
  toast(`Unexpected error: ${err?.message ?? String(e.reason)}`, "error", 7000);
});

installTooltips();
if (import.meta.env.DEV) (window as unknown as Record<string, unknown>).__editor = { store, media, get preview() { return previewRef; }, get tv() { return tvRef; } };
void boot();
