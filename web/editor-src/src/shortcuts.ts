// Global keyboard shortcuts (Premiere-style defaults).

import * as actions from "./actions";
import { autosave } from "./autosave";
import { activeMonitor, type SourceMonitor } from "./panels/monitors";
import { openShortcuts } from "./panels/dialogs";
import type { Preview } from "./preview";
import { store } from "./store";
import type { TimelineView } from "./timeline-view";
import { isTyping, modalOpen, toast } from "./ui";

export interface ShortcutDeps {
  preview: Preview;
  source: SourceMonitor;
  timeline: TimelineView;
  toggleAi: () => void;
  closeDrawers: () => boolean;
}

export function installShortcuts(d: ShortcutDeps): void {
  let shuttle = 0;
  const setShuttle = (rate: number) => {
    shuttle = rate;
    if (activeMonitor === "source") return d.source.shuttle(rate);
    if (rate === 0) d.preview.pause();
    else d.preview.play(rate);
  };

  window.addEventListener("keydown", (e) => {
    if (modalOpen() || isTyping(e)) return;
    const mod = e.ctrlKey || e.metaKey;
    const k = e.key;
    const lower = k.length === 1 ? k.toLowerCase() : k;
    let handled = true;
    const src = activeMonitor === "source";
    if (mod) {
      if (lower === "z" && !e.shiftKey) {
        const l = store.undo();
        if (l) toast(`Undo: ${l}`, "info", 1400);
      } else if ((lower === "z" && e.shiftKey) || lower === "y") {
        const l = store.redo();
        if (l) toast(`Redo: ${l}`, "info", 1400);
      } else if (lower === "s") void autosave.saveVersion();
      else if (lower === "k") actions.splitAtPlayhead();
      else if (lower === "c") actions.copySelection();
      else if (lower === "x") actions.copySelection(true);
      else if (lower === "v") actions.pasteAtPlayhead();
      else if (lower === "a") store.select(store.doc.tracks.flatMap((t) => t.clips.map((c) => c.id)));
      else if (lower === "l") actions.linkSelection();
      else if (k === "=" || k === "+") d.timeline.zoomIn();
      else if (k === "-") d.timeline.zoomOut();
      else handled = false;
    } else {
      switch (lower) {
        case " ":
          if (src) d.source.toggle();
          else d.preview.toggle();
          shuttle = 0;
          break;
        case "l":
          setShuttle(shuttle <= 0 ? 1 : Math.min(8, shuttle * 2));
          break;
        case "j":
          setShuttle(shuttle >= 0 ? -1 : Math.max(-8, shuttle * 2));
          break;
        case "k":
          setShuttle(0);
          break;
        case "ArrowLeft":
          if (e.altKey) actions.nudgeSelection(-1);
          else if (src) d.source.step(e.shiftKey ? -10 : -1);
          else {
            d.preview.pause();
            store.setPlayhead(store.playhead - (e.shiftKey ? 10 : 1));
          }
          break;
        case "ArrowRight":
          if (e.altKey) actions.nudgeSelection(1);
          else if (src) d.source.step(e.shiftKey ? 10 : 1);
          else {
            d.preview.pause();
            store.setPlayhead(store.playhead + (e.shiftKey ? 10 : 1));
          }
          break;
        case "ArrowUp":
          d.preview.pause();
          actions.jumpEdit(-1);
          break;
        case "ArrowDown":
          d.preview.pause();
          actions.jumpEdit(1);
          break;
        case "Home":
          if (src) d.source.home();
          else store.setPlayhead(0);
          break;
        case "End":
          if (src) d.source.end();
          else actions.goEnd();
          break;
        case "i":
          if (src) d.source.mark("in");
          else actions.markIn();
          break;
        case "o":
          if (src) d.source.mark("out");
          else actions.markOut();
          break;
        case "v":
          store.setTool("select");
          break;
        case "c":
          store.setTool("razor");
          break;
        case "q":
          actions.rippleTrimPlayhead("head");
          break;
        case "w":
          actions.rippleTrimPlayhead("tail");
          break;
        case "Delete":
        case "Backspace":
          actions.deleteSelection(e.shiftKey);
          break;
        case ",":
          d.source.edit("insert");
          break;
        case ".":
          d.source.edit("overwrite");
          break;
        case "t":
          actions.addTitle();
          break;
        case "m":
          actions.addMarker();
          break;
        case "s":
          store.snapping = !store.snapping;
          store.emit("tool");
          toast(`Snapping ${store.snapping ? "on" : "off"}`, "info", 1200);
          break;
        case "e":
          if (e.shiftKey) actions.toggleEnabled();
          else handled = false;
          break;
        case "=":
        case "+":
          d.timeline.zoomIn();
          break;
        case "-":
        case "_":
          d.timeline.zoomOut();
          break;
        case "\\":
          d.timeline.zoomFit();
          break;
        case "?":
          openShortcuts();
          break;
        case "a":
          if (e.shiftKey) d.toggleAi();
          else handled = false;
          break;
        case "Escape":
          if (!d.closeDrawers()) store.clearSelection();
          break;
        default:
          handled = false;
      }
    }
    if (handled) e.preventDefault();
  });
}
