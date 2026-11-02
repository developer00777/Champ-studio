// Autosave (debounced PUT with optimistic concurrency), conflict handling,
// offline retry, and "Save version" snapshots.

import { ApiError, get, post, put, type Project } from "./api";
import { store } from "./store";
import { confirmDialog, promptDialog, toast } from "./ui";

export type SaveState = { kind: "saved"; version: number } | { kind: "dirty" } | { kind: "saving" } | { kind: "conflict" } | { kind: "offline"; retryIn: number } | { kind: "locked"; by: string } | { kind: "error"; message: string } | { kind: "readonly" };

const DEBOUNCE_MS = 2000;

class Autosave {
  state: SaveState = { kind: "saved", version: 0 };
  private timer = 0;
  private savedRevision = 0;
  private inflight: Promise<void> | null = null;
  private retryDelay = 2000;
  private conflictOpen = false;
  pendingName: string | null = null;

  init(): void {
    store.on("doc", () => {
      if (store.revision !== this.savedRevision && !store.readOnly) this.schedule();
    });
    window.addEventListener("online", () => this.state.kind === "offline" && this.flush());
    window.addEventListener("beforeunload", (e) => {
      if (this.dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    });
    this.savedRevision = store.revision;
    this.set({ kind: "saved", version: store.project?.version ?? 0 });
  }

  get dirty(): boolean {
    return (store.revision !== this.savedRevision || this.pendingName !== null) && !store.readOnly;
  }

  private set(s: SaveState): void {
    this.state = s;
    store.emit("save");
  }

  /** Mark the current document as saved (after load/reload). */
  markClean(): void {
    this.savedRevision = store.revision;
    clearTimeout(this.timer);
    this.set(store.readOnly ? { kind: "readonly" } : { kind: "saved", version: store.project?.version ?? 0 });
  }

  schedule(): void {
    if (this.state.kind === "conflict") return;
    clearTimeout(this.timer);
    if (this.state.kind !== "saving" && this.state.kind !== "offline") this.set({ kind: "dirty" });
    this.timer = window.setTimeout(() => void this.flush(), DEBOUNCE_MS);
  }

  /** Save now (resolves when the document on the server matches the editor). */
  async flush(force = false): Promise<void> {
    clearTimeout(this.timer);
    if (this.inflight) {
      await this.inflight;
      if (this.dirty) return this.flush(force);
      return;
    }
    if (!this.dirty && !force) return;
    if (!store.project || store.readOnly) return;
    this.inflight = this.save(force).finally(() => (this.inflight = null));
    return this.inflight;
  }

  private async save(overwrite: boolean): Promise<void> {
    const p = store.project!;
    const rev = store.revision;
    const data = store.doc;
    this.set({ kind: "saving" });
    try {
      const body: Record<string, unknown> = { data };
      if (!overwrite) body.base_version = p.version;
      if (this.pendingName) body.name = this.pendingName;
      const saved = await put<Project>(`/api/projects/${p.id}`, body);
      store.project = { ...p, ...saved, data: p.data };
      this.savedRevision = rev;
      this.pendingName = null;
      this.retryDelay = 2000;
      this.set({ kind: "saved", version: saved.version });
      store.emit("project");
      if (store.revision !== rev) this.schedule();
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 409) {
        this.set({ kind: "conflict" });
        void this.conflict();
      } else if (err.status === 423) {
        this.set({ kind: "locked", by: err.detail });
        toast(`Can't save: ${err.detail}`, "error", 7000);
      } else if (err.status === 0 || err.status >= 500) {
        const wait = this.retryDelay;
        this.retryDelay = Math.min(30000, this.retryDelay * 2);
        this.set({ kind: "offline", retryIn: wait });
        this.timer = window.setTimeout(() => void this.flush(), wait);
      } else {
        this.set({ kind: "error", message: err.detail || String(e) });
        toast(`Save failed: ${err.detail || err.message}`, "error", 7000);
      }
    }
  }

  private async conflict(): Promise<void> {
    if (this.conflictOpen) return;
    this.conflictOpen = true;
    const choice = await confirmDialog(
      "Someone else saved this sequence",
      "A teammate saved a newer version while you were editing. Reload their version (your unsaved changes are discarded), or overwrite it with yours.",
      [
        { label: "Reload theirs", value: "reload" },
        { label: "Overwrite with mine", value: "overwrite", danger: true, primary: true },
      ],
    );
    this.conflictOpen = false;
    if (choice === "reload") await this.reload();
    else if (choice === "overwrite") {
      this.set({ kind: "dirty" });
      await this.flush(true);
    } else this.set({ kind: "conflict" });
  }

  async reload(): Promise<void> {
    const p = await get<Project>(`/api/projects/${store.project!.id}`);
    store.project = p;
    store.load(p.data);
    this.markClean();
    store.emit("project");
    toast(`Reloaded v${p.version}`, "info");
  }

  /** Ctrl/Cmd+S: save now, then snapshot a named version. */
  async saveVersion(): Promise<void> {
    if (store.readOnly || !store.project) return;
    const label = await promptDialog("Save version", "Version label", `Version ${new Date().toLocaleString()}`, "Save version");
    if (label === null) return;
    await this.flush();
    if (this.state.kind !== "saved") {
      toast("Save the sequence first (it has unsaved or conflicting changes)", "warn");
      return;
    }
    try {
      await post(`/api/projects/${store.project.id}/versions`, { label });
      toast(`Saved version “${label}”`, "success");
    } catch (e) {
      toast(`Couldn't save version: ${(e as Error).message}`, "error");
    }
  }
}

export const autosave = new Autosave();
