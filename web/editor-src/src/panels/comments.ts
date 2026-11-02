// Review comments: list + composer; shown as timeline markers. Reviewers and
// viewers can comment even though the sequence is read-only for them.

import { ApiError, get, post, put, type Comment } from "../api";
import { timecode } from "../model";
import { store } from "../store";
import type { TimelineView } from "../timeline-view";
import { button, clear, h, icon, toast } from "../ui";

export class CommentsPanel {
  readonly el: HTMLElement;
  private list: HTMLElement;
  private comments: Comment[] = [];
  private unavailable = false;
  private showResolved = false;
  private composer: HTMLTextAreaElement;
  private rangeChk: HTMLInputElement;
  private timeline: TimelineView | null = null;
  private loaded = false;
  onCountChange: (open: number) => void = () => {};

  constructor() {
    this.list = h("div", { class: "comment-list" });
    this.composer = h("textarea", { class: "input", rows: 2, placeholder: "Comment at the playhead…", "aria-label": "New comment" });
    this.rangeChk = h("input", { type: "checkbox" });
    const resolvedChk = h("input", { type: "checkbox" });
    resolvedChk.addEventListener("change", () => {
      this.showResolved = resolvedChk.checked;
      this.render();
    });
    this.composer.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void this.add();
      e.stopPropagation();
    });
    this.el = h(
      "div",
      { class: "comments-panel" },
      h("div", { class: "composer" }, this.composer, h("div", { class: "row gap" }, h("label", { class: "field check" }, this.rangeChk, h("span", null, "Use In/Out range")), h("span", { class: "grow" }), button("Comment", () => void this.add(), "btn btn-primary btn-sm", undefined, "Ctrl/Cmd+Enter"))),
      h("label", { class: "field check" }, resolvedChk, h("span", null, "Show resolved")),
      this.list,
    );
  }

  attach(tv: TimelineView): void {
    this.timeline = tv;
    tv.onCommentClick = (id) => {
      const el = this.list.querySelector<HTMLElement>(`[data-id="${CSS.escape(id)}"]`);
      el?.scrollIntoView({ block: "nearest" });
      el?.classList.add("flash");
      setTimeout(() => el?.classList.remove("flash"), 900);
    };
  }

  async load(): Promise<void> {
    const p = store.project;
    if (!p) return;
    this.loaded = true;
    try {
      const r = await get<{ comments: Comment[] } | Comment[]>(`/api/projects/${p.id}/comments`);
      this.comments = Array.isArray(r) ? r : r.comments ?? [];
      this.unavailable = false;
    } catch (e) {
      this.comments = [];
      this.unavailable = e instanceof ApiError && e.unavailable;
      if (!this.unavailable) toast(`Couldn't load comments: ${(e as Error).message}`, "error");
    }
    this.render();
  }

  private sync(): void {
    if (this.timeline) {
      this.timeline.commentMarkers = this.comments.filter((c) => this.showResolved || !c.resolved).map((c) => ({ f: c.in_frame, out: c.out_frame, label: c.body, resolved: c.resolved, id: c.id }));
      this.timeline.invalidate();
    }
    this.onCountChange(this.comments.filter((c) => !c.resolved).length);
  }

  private render(): void {
    clear(this.list);
    this.sync();
    if (!this.loaded) return;
    if (this.unavailable) {
      this.list.append(h("div", { class: "empty" }, icon("comment", 24), h("p", null, "Review comments aren't enabled on this engine yet."), h("p", { class: "muted" }, "Comments will appear here and as blue markers on the timeline.")));
      return;
    }
    const items = this.comments.filter((c) => this.showResolved || !c.resolved).sort((a, b) => a.in_frame - b.in_frame);
    if (!items.length) {
      this.list.append(h("div", { class: "empty" }, icon("comment", 24), h("p", null, "No comments yet."), h("p", { class: "muted" }, "Park the playhead and leave a note for the team.")));
      return;
    }
    const fps = store.fps;
    for (const c of items) {
      const resolve = h("input", { type: "checkbox", checked: !!c.resolved, "aria-label": "Resolved" });
      resolve.addEventListener("change", () => void this.setResolved(c, resolve.checked));
      this.list.append(
        h(
          "div",
          { class: `comment${c.resolved ? " resolved" : ""}`, "data-id": c.id },
          h(
            "div",
            { class: "comment-head" },
            h("button", { class: "linklike mono", type: "button", onclick: () => store.setPlayhead(c.in_frame) }, timecode(c.in_frame, fps), c.out_frame != null && c.out_frame > c.in_frame ? `–${timecode(c.out_frame, fps)}` : ""),
            h("span", { class: "muted grow ellipsis" }, c.author ?? ""),
            h("label", { class: "check", "data-tip": "Mark resolved" }, resolve, h("span", null, "Done")),
          ),
          h("div", { class: "comment-body" }, c.body),
        ),
      );
    }
  }

  private async add(): Promise<void> {
    const p = store.project;
    const body = this.composer.value.trim();
    if (!p || !body) return;
    const useRange = this.rangeChk.checked && store.markIn !== null;
    const payload = { body, in_frame: useRange ? store.markIn! : store.playhead, out_frame: useRange ? store.markOut ?? store.markIn! : store.playhead, version: p.version };
    try {
      const c = await post<Comment>(`/api/projects/${p.id}/comments`, payload);
      this.comments.push(c && c.id ? c : { id: `local-${Date.now()}`, ...payload, author: store.me.email });
      this.composer.value = "";
      this.render();
    } catch (e) {
      if (e instanceof ApiError && e.unavailable) toast("Comments aren't available on this engine yet", "warn");
      else toast(`Couldn't post comment: ${(e as Error).message}`, "error");
    }
  }

  private async setResolved(c: Comment, resolved: boolean): Promise<void> {
    try {
      await put(`/api/comments/${c.id}`, { resolved });
      c.resolved = resolved;
    } catch (e) {
      toast(`Couldn't update comment: ${(e as Error).message}`, "error");
    }
    this.render();
  }
}
