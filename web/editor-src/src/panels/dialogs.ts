// Export, Versions and Shortcut-sheet dialogs.

import { ApiError, downloadUrl, get, post, waitJob, type Job, type Project, type Version } from "../api";
import { autosave } from "../autosave";
import { store } from "../store";
import { button, clear, fmtDate, h, modal, progressBar, toast } from "../ui";

// ── export ───────────────────────────────────────────────────────────

let activeRender: { jobId: string; abort: AbortController } | null = null;

export async function openExport(): Promise<void> {
  const p = store.project;
  if (!p) return;
  const m = modal("Export", { wide: true });
  const presetSel = h("select", { class: "input", "aria-label": "Preset" });
  const nameIn = h("input", { class: "input", type: "text", value: p.name, "aria-label": "Output name" });
  const status = h("div", { class: "export-status" });
  const prog = progressBar();
  prog.el.style.display = "none";
  const result = h("div", { class: "export-result" });
  const renderBtn = h("button", { class: "btn btn-primary", type: "button" }, "Render");
  const cancelBtn = h("button", { class: "btn btn-danger", type: "button", style: "display:none" }, "Cancel render");
  const fmtSel = h("select", { class: "input", "aria-label": "Interchange format" });
  const fileIn = h("input", { type: "file", accept: ".xml,.fcpxml,.edl,.otio", style: "display:none" });

  m.body.append(
    h("h3", { class: "dlg-h" }, "Render video"),
    h("div", { class: "grid2" }, h("label", { class: "field" }, h("span", { class: "field-label" }, "Preset"), presetSel), h("label", { class: "field" }, h("span", { class: "field-label" }, "Name"), nameIn)),
    h("div", { class: "row gap" }, renderBtn, cancelBtn),
    prog.el,
    status,
    result,
    h("hr"),
    h("h3", { class: "dlg-h" }, "Captions"),
    h(
      "div",
      { class: "row gap" },
      button("Download SRT", () => void downloadSaved(`/api/projects/${p.id}/captions.srt`, `${p.name}.srt`), "btn btn-sm"),
      button("Download VTT", () => void downloadSaved(`/api/projects/${p.id}/captions.vtt`, `${p.name}.vtt`), "btn btn-sm"),
    ),
    h("h3", { class: "dlg-h" }, "Interchange (Premiere Pro / Resolve / Avid)"),
    h(
      "div",
      { class: "row gap" },
      fmtSel,
      button("Download package (.zip)", () => void downloadSaved(`/api/projects/${p.id}/interchange?format=${fmtSel.value}&package=true`, `${p.name}-${fmtSel.value}.zip`), "btn btn-sm"),
      button("Import XML / EDL / OTIO…", () => fileIn.click(), "btn btn-sm"),
      fileIn,
    ),
    h("p", { class: "hint" }, "Packages bundle the edit list with relinkable media. Import creates a new sequence."),
  );
  m.footer.append(button("Close", () => m.close()));

  // presets + formats
  try {
    const res = await get<{ presets: Record<string, { label: string; ext: string }> }>("/api/render/presets");
    for (const [id, v] of Object.entries(res.presets)) presetSel.append(h("option", { value: id }, `${v.label} (${v.ext})`));
  } catch (e) {
    status.textContent = `Couldn't load presets: ${(e as Error).message}`;
  }
  try {
    const res = await get<{ formats: string[] }>("/api/interchange/formats");
    const labels: Record<string, string> = { fcp_xml: "Premiere / FCP7 XML", edl: "CMX 3600 EDL", otio: "OpenTimelineIO" };
    for (const f of res.formats) fmtSel.append(h("option", { value: f }, labels[f] ?? f));
  } catch {
    fmtSel.append(h("option", { value: "fcp_xml" }, "Premiere / FCP7 XML"));
  }

  const watch = async (jobId: string) => {
    const abort = new AbortController();
    activeRender = { jobId, abort };
    renderBtn.disabled = true;
    cancelBtn.style.display = "";
    prog.el.style.display = "";
    clear(result);
    try {
      const job = await waitJob<{ asset?: { id: string; url: string } }>(jobId, {
        signal: abort.signal,
        onProgress: (j: Job) => prog.set(j.progress ?? 0, `${j.stage || j.status} · ${Math.round((j.progress ?? 0) * 100)}%`),
      });
      prog.set(1, "Done");
      const a = job.result?.asset;
      if (a) {
        const url = a.url || `/api/assets/${a.id}/file`;
        result.append(h("a", { class: "btn btn-primary", href: url, download: "", target: "_blank", rel: "noopener" }, "Download render"), h("span", { class: "muted" }, ` Saved to the asset library (${a.id}).`));
      }
      toast("Render complete", "success");
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      const msg = e instanceof ApiError && e.status === 499 ? "Render canceled" : `Render failed: ${(e as Error).message}`;
      status.textContent = msg;
      toast(msg, e instanceof ApiError && e.status === 499 ? "info" : "error", 7000);
    } finally {
      renderBtn.disabled = false;
      cancelBtn.style.display = "none";
      activeRender = null;
    }
  };

  renderBtn.addEventListener("click", async () => {
    status.textContent = "";
    await autosave.flush();
    try {
      const r = await post<{ job_id: string }>(`/api/projects/${p.id}/render`, { preset: presetSel.value, timeline: store.doc, name: nameIn.value });
      void watch(r.job_id);
    } catch (e) {
      status.textContent = `Couldn't start render: ${(e as Error).message}`;
    }
  });
  cancelBtn.addEventListener("click", async () => {
    if (!activeRender) return;
    try {
      await post(`/api/jobs/${activeRender.jobId}/cancel`);
      status.textContent = "Canceling…";
    } catch (e) {
      toast(`Cancel failed: ${(e as Error).message}`, "error");
    }
  });
  fileIn.addEventListener("change", async () => {
    const f = fileIn.files?.[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    try {
      const res = await fetch("/api/projects/import", { method: "POST", body: fd, credentials: "same-origin" });
      if (!res.ok) throw new ApiError(res.status, (await res.json().catch(() => ({}))).detail ?? res.statusText);
      const proj = (await res.json()) as Project;
      toast(`Imported “${proj.name}”`, "success", 6000, { label: "Open", run: () => openProject(proj.id) });
    } catch (e) {
      toast(`Import failed: ${(e as Error).message}`, "error", 7000);
    }
    fileIn.value = "";
  });
  m.onClose(() => activeRender?.abort.abort());
  if (activeRender) void watch(activeRender.jobId);
}

/** Caption/interchange exports read the saved project — save first. */
async function downloadSaved(url: string, filename: string): Promise<void> {
  await autosave.flush();
  try {
    const res = await fetch(url, { credentials: "same-origin" });
    if (!res.ok) throw new ApiError(res.status, (await res.json().catch(() => ({}))).detail ?? res.statusText);
    const blob = await res.blob();
    const obj = URL.createObjectURL(blob);
    downloadUrl(obj, filename);
    setTimeout(() => URL.revokeObjectURL(obj), 10000);
  } catch (e) {
    toast(`Download failed: ${(e as Error).message}`, "error");
  }
}

export function openProject(id: string): void {
  const u = new URL(location.href);
  u.searchParams.set("project", id);
  window.open(u.toString(), "_blank", "noopener");
}

// ── versions ─────────────────────────────────────────────────────────

export async function openVersions(): Promise<void> {
  const p = store.project;
  if (!p) return;
  const m = modal("Versions", { wide: true });
  const list = h("div", { class: "version-list" }, h("p", { class: "muted" }, "Loading…"));
  m.body.append(h("p", { class: "hint" }, "Named versions are snapshots of the sequence. Restoring keeps a backup of the current state and can be undone."), list);
  m.footer.append(button("Save version… (Ctrl+S)", () => { m.close(); void autosave.saveVersion(); }, "btn"), button("Close", () => m.close()));
  try {
    const res = await get<{ versions: Version[] }>(`/api/projects/${p.id}/versions`);
    clear(list);
    if (!res.versions.length) list.append(h("div", { class: "empty" }, h("p", null, "No saved versions yet."), h("p", { class: "muted" }, "Press Ctrl/Cmd+S to save a named version.")));
    for (const v of res.versions) {
      list.append(
        h(
          "div",
          { class: "version-row" },
          h("span", { class: "mono" }, `v${v.version}`),
          h("span", { class: "grow" }, v.label || "Untitled", h("span", { class: "muted" }, ` · ${v.author ?? ""} ${fmtDate(v.created_at)}`)),
          store.readOnly
            ? null
            : button("Restore", async () => {
                await autosave.flush();
                try {
                  const restored = await post<Project>(`/api/projects/${p.id}/versions/${v.version}/restore`);
                  store.project = { ...restored };
                  store.replaceFromServer(restored.data, `Restore v${v.version}`);
                  autosave.markClean();
                  store.emit("project");
                  toast(`Restored version v${v.version}`, "success");
                  m.close();
                } catch (e) {
                  toast(`Restore failed: ${(e as Error).message}`, "error");
                }
              }, "btn btn-sm"),
        ),
      );
    }
  } catch (e) {
    clear(list);
    list.append(h("p", { class: "error" }, `Couldn't load versions: ${(e as Error).message}`));
  }
}

// ── shortcuts ────────────────────────────────────────────────────────

export const SHORTCUTS: Array<[string, Array<[string, string]>]> = [
  [
    "Playback",
    [
      ["Space", "Play / pause"],
      ["J / K / L", "Shuttle reverse / stop / forward (repeat L or J for 2×, 4×)"],
      ["← / →", "Step 1 frame (Shift: 10 frames)"],
      ["↑ / ↓", "Previous / next edit point"],
      ["Home / End", "Go to start / end"],
      ["I / O", "Mark in / out (active monitor)"],
    ],
  ],
  [
    "Editing",
    [
      ["V / C", "Selection tool / Razor tool"],
      ["Ctrl/Cmd + K", "Add edit at playhead (targeted tracks or selected clips)"],
      ["Q / W", "Ripple trim previous / next edit to playhead"],
      ["Delete", "Lift (leave a gap)"],
      ["Shift + Delete", "Ripple delete"],
      [", / .", "Insert / overwrite from Source monitor"],
      ["Ctrl/Cmd + C / X / V", "Copy / cut / paste at playhead"],
      ["Alt + ← / →", "Nudge selected clips one frame"],
      ["Alt + drag edge", "Roll edit"],
      ["Alt + click", "Select one side of a linked clip"],
      ["Ctrl/Cmd + L", "Link / unlink selected clips"],
      ["Shift + E", "Enable / disable clips"],
      ["T", "Add title at playhead"],
      ["M", "Add marker"],
      ["S", "Toggle snapping (hold Shift while dragging to invert)"],
    ],
  ],
  [
    "Timeline & app",
    [
      ["= / -", "Zoom in / out"],
      ["\\", "Zoom to fit"],
      ["Ctrl/Cmd + wheel", "Zoom at pointer"],
      ["Shift + wheel", "Scroll tracks vertically"],
      ["Ctrl/Cmd + Z", "Undo"],
      ["Ctrl/Cmd + Shift + Z", "Redo"],
      ["Ctrl/Cmd + S", "Save a named version"],
      ["Ctrl/Cmd + A", "Select all clips"],
      ["?", "This sheet"],
    ],
  ],
];

export function openShortcuts(): void {
  const m = modal("Keyboard shortcuts", { wide: true });
  const grid = h("div", { class: "shortcut-grid" });
  for (const [title, rows] of SHORTCUTS) {
    grid.append(h("div", null, h("h3", { class: "dlg-h" }, title), h("dl", { class: "shortcuts" }, rows.flatMap(([k, d]) => [h("dt", null, h("kbd", null, k)), h("dd", null, d)]))));
  }
  m.body.append(grid);
  m.footer.append(button("Close", () => m.close(), "btn btn-primary"));
}
