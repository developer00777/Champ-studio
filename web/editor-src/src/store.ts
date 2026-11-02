// Editor state: the timeline document + an undo/redo command stack.
// Every user action is one command holding before/after snapshots of the
// (immutable) document; drags preview through `transient` and commit once.

import type { Brand, Me, Project } from "./api";
import { cloneTimeline, findClip, normalizeTimeline, timelineDuration, type Timeline } from "./model";
import type { OpCtx } from "./ops";

export type Listener = () => void;

export class Emitter<E extends string> {
  private map = new Map<E, Set<Listener>>();
  on(name: E, fn: Listener): () => void {
    let s = this.map.get(name);
    if (!s) this.map.set(name, (s = new Set()));
    s.add(fn);
    return () => s!.delete(fn);
  }
  emit(name: E): void {
    for (const fn of [...(this.map.get(name) ?? [])]) {
      try {
        fn();
      } catch (e) {
        console.error(e);
      }
    }
  }
}

interface Command {
  label: string;
  before: Timeline;
  after: Timeline;
  key?: string;
  at: number;
}

export type Tool = "select" | "razor";
export type StoreEvent = "doc" | "selection" | "playhead" | "history" | "tool" | "project" | "readonly" | "save" | "marks";

const MAX_HISTORY = 300;

export class Store extends Emitter<StoreEvent> {
  project: Project | null = null;
  me: Me = { id: "local", email: "local", role: "admin" };
  readOnly = false;
  readOnlyReason = "";
  doc: Timeline = normalizeTimeline({});
  transient: Timeline | null = null;
  selection = new Set<string>();
  playhead = 0;
  tool: Tool = "select";
  snapping = true;
  linkedSelection = true;
  targets = new Set<string>();
  markIn: number | null = null;
  markOut: number | null = null;
  /** Bumped on every committed document change (autosave watches it). */
  revision = 0;
  ctx: OpCtx = {};
  brand: Brand | null = null;
  private undoStack: Command[] = [];
  private redoStack: Command[] = [];

  get view(): Timeline {
    return this.transient ?? this.doc;
  }

  get fps(): number {
    return this.doc.fps;
  }

  get duration(): number {
    return timelineDuration(this.view);
  }

  load(doc: Timeline): void {
    this.doc = normalizeTimeline(doc);
    this.transient = null;
    this.undoStack = [];
    this.redoStack = [];
    this.pruneSelection();
    if (!this.targets.size) this.defaultTargets();
    this.emit("doc");
    this.emit("history");
  }

  defaultTargets(): void {
    this.targets.clear();
    const v = this.doc.tracks.find((t) => t.kind === "video");
    const a = this.doc.tracks.find((t) => t.kind === "audio");
    if (v) this.targets.add(v.id);
    if (a) this.targets.add(a.id);
  }

  setReadOnly(on: boolean, reason = ""): void {
    this.readOnly = on;
    this.readOnlyReason = reason;
    this.emit("readonly");
  }

  /** Apply one undoable change. `change` returns the new document (ops are pure). */
  apply(label: string, change: ((tl: Timeline) => Timeline) | Timeline, opts: { coalesce?: string } = {}): boolean {
    if (this.readOnly) return false;
    const before = this.doc;
    const after = typeof change === "function" ? change(before) : change;
    if (after === before) return false;
    const last = this.undoStack[this.undoStack.length - 1];
    if (opts.coalesce && last && last.key === opts.coalesce && Date.now() - last.at < 2000 && !this.redoStack.length) {
      last.after = after;
      last.at = Date.now();
    } else {
      this.undoStack.push({ label, before, after, key: opts.coalesce, at: Date.now() });
      if (this.undoStack.length > MAX_HISTORY) this.undoStack.shift();
    }
    this.redoStack = [];
    this.setDoc(after);
    return true;
  }

  /** Show an in-progress edit (drag) without touching history. */
  preview(tl: Timeline | null): void {
    this.transient = tl;
    this.emit("doc");
  }

  /** Commit the transient document as one command. */
  commitPreview(label: string): void {
    const t = this.transient;
    this.transient = null;
    if (t && t !== this.doc) this.apply(label, t);
    else this.emit("doc");
  }

  private setDoc(tl: Timeline): void {
    this.doc = tl;
    this.transient = null;
    this.revision++;
    this.pruneSelection();
    this.emit("doc");
    this.emit("history");
  }

  /** Replace the document from the server (reload/restore) as an undoable step or not. */
  replaceFromServer(tl: Timeline, label?: string): void {
    const n = normalizeTimeline(tl);
    if (label && !this.readOnly) {
      this.undoStack.push({ label, before: this.doc, after: n, at: Date.now() });
      this.redoStack = [];
      this.setDoc(n);
    } else {
      this.load(n);
    }
  }

  canUndo(): boolean {
    return this.undoStack.length > 0 && !this.readOnly;
  }
  canRedo(): boolean {
    return this.redoStack.length > 0 && !this.readOnly;
  }
  undoLabel(): string {
    return this.undoStack[this.undoStack.length - 1]?.label ?? "";
  }
  redoLabel(): string {
    return this.redoStack[this.redoStack.length - 1]?.label ?? "";
  }

  undo(): string | null {
    if (!this.canUndo()) return null;
    const c = this.undoStack.pop()!;
    this.redoStack.push(c);
    this.setDoc(c.before);
    return c.label;
  }

  redo(): string | null {
    if (!this.canRedo()) return null;
    const c = this.redoStack.pop()!;
    this.undoStack.push(c);
    this.setDoc(c.after);
    return c.label;
  }

  // ── selection / playhead ──

  select(ids: Iterable<string>, mode: "set" | "add" | "toggle" = "set"): void {
    if (mode === "set") this.selection = new Set(ids);
    else for (const id of ids) {
      if (mode === "toggle" && this.selection.has(id)) this.selection.delete(id);
      else this.selection.add(id);
    }
    this.emit("selection");
  }

  clearSelection(): void {
    if (!this.selection.size) return;
    this.selection.clear();
    this.emit("selection");
  }

  private pruneSelection(): void {
    let changed = false;
    for (const id of this.selection) {
      if (!findClip(this.doc, id)) {
        this.selection.delete(id);
        changed = true;
      }
    }
    for (const t of [...this.targets]) if (!this.doc.tracks.some((x) => x.id === t)) this.targets.delete(t);
    if (changed) this.emit("selection");
  }

  /** The single primary selected clip (first in document order). */
  primary(): ReturnType<typeof findClip> {
    for (const t of this.view.tracks) for (const c of t.clips) if (this.selection.has(c.id)) return findClip(this.view, c.id);
    return null;
  }

  setPlayhead(f: number): void {
    const v = Math.max(0, Math.round(f));
    if (v === this.playhead) return;
    this.playhead = v;
    this.emit("playhead");
  }

  setTool(t: Tool): void {
    this.tool = t;
    this.emit("tool");
  }

  setMarks(i: number | null, o: number | null): void {
    this.markIn = i;
    this.markOut = o;
    this.emit("marks");
  }

  snapshot(): Timeline {
    return cloneTimeline(this.doc);
  }
}

export const store = new Store();
