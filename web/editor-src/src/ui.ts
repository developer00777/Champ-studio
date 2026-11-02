// Small DOM toolkit: element builder, toasts, modal dialogs, icons.

type Child = Node | string | number | null | undefined | false;
type Attrs = Record<string, unknown> & { class?: string; style?: string | Partial<CSSStyleDeclaration> };

export function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Attrs | null = null, ...children: Array<Child | Child[]>): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") el.className = String(v);
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v as EventListener);
      else if (k === "html") el.innerHTML = String(v);
      else if (k in el && k !== "list" && typeof v !== "string") (el as unknown as Record<string, unknown>)[k] = v;
      else el.setAttribute(k, v === true ? "" : String(v));
    }
  }
  append(el, children);
  return el;
}

function append(el: Element, children: Array<Child | Child[]>): void {
  for (const c of children) {
    if (Array.isArray(c)) append(el, c);
    else if (c === null || c === undefined || c === false) continue;
    else el.append(c instanceof Node ? c : String(c));
  }
}

export function clear(el: Element): void {
  while (el.firstChild) el.firstChild.remove();
}

// ── icons (inline SVG, 16px, currentColor) ───────────────────────────

const P: Record<string, string> = {
  undo: "M4 7h7a4 4 0 0 1 0 8H7M4 7l3-3M4 7l3 3",
  redo: "M12 7H5a4 4 0 0 0 0 8h4M12 7L9 4M12 7l-3 3",
  play: "M5 3l9 5-9 5z",
  pause: "M5 3h2v10H5zM9 3h2v10H9z",
  stepb: "M11 3v10L5 8zM4 3v10",
  stepf: "M5 3v10l6-5zM12 3v10",
  lock: "M4 7h8v6H4zM6 7V5a2 2 0 0 1 4 0v2",
  unlock: "M4 7h8v6H4zM6 7V5a2 2 0 0 1 4 0",
  eye: "M1 8s3-5 7-5 7 5 7 5-3 5-7 5-7-5-7-5zM8 6a2 2 0 1 0 0 4 2 2 0 0 0 0-4z",
  eyeoff: "M2 2l12 12M1 8s3-5 7-5c1.5 0 2.7.6 3.8 1.4M15 8s-3 5-7 5c-1.5 0-2.7-.6-3.8-1.4",
  speaker: "M2 6h3l4-3v10l-4-3H2zM11 5.5a3 3 0 0 1 0 5",
  mute: "M2 6h3l4-3v10l-4-3H2zM11 6l4 4M15 6l-4 4",
  razor: "M8 1v14M4 4l8 8M12 4l-8 8",
  cursor: "M3 2l9 5-4 1-1 4z",
  magnet: "M4 2v6a4 4 0 0 0 8 0V2M4 5h3M9 5h3",
  link: "M6 10l4-4M5 7l-1.5 1.5a2.5 2.5 0 0 0 3.5 3.5L8.5 10.5M11 9l1.5-1.5A2.5 2.5 0 0 0 9 4L7.5 5.5",
  plus: "M8 3v10M3 8h10",
  text: "M3 3h10M8 3v10",
  sparkle: "M8 1l1.5 4.5L14 7l-4.5 1.5L8 13l-1.5-4.5L2 7l4.5-1.5z",
  export: "M8 10V2M5 5l3-3 3 3M3 10v4h10v-4",
  history: "M2 8a6 6 0 1 0 2-4.5M2 2v3h3M8 5v3l2 2",
  upload: "M8 11V3M5 6l3-3 3 3M3 13h10",
  close: "M4 4l8 8M12 4l-8 8",
  diamond: "M8 2l6 6-6 6-6-6z",
  chevl: "M10 3L5 8l5 5",
  chevr: "M6 3l5 5-5 5",
  marker: "M4 2h8v8l-4 4-4-4z",
  comment: "M2 3h12v8H7l-3 3v-3H2z",
  grid: "M2 2h5v5H2zM9 2h5v5H9zM2 9h5v5H2zM9 9h5v5H9z",
  keyboard: "M1 4h14v8H1zM4 7h1M7 7h1M10 7h1M4 10h8",
  film: "M2 2h12v12H2zM5 2v12M11 2v12M2 5h3M2 8h3M2 11h3M11 5h3M11 8h3M11 11h3",
  music: "M6 12V3l7-1v9M6 12a2 2 0 1 1-4 0 2 2 0 0 1 4 0zM13 11a2 2 0 1 1-4 0 2 2 0 0 1 4 0z",
  image: "M2 3h12v10H2zM2 11l4-4 3 3 2-2 3 3",
  search: "M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10zM11 11l4 4",
  target: "M8 2v3M8 11v3M2 8h3M11 8h3M8 5a3 3 0 1 1 0 6 3 3 0 0 1 0-6z",
  solo: "M5 11c0 1 1 2 3 2s3-1 3-2-1-2-3-2-3-1-3-2 1-2 3-2 3 1 3 2",
  check: "M3 8l3 3 7-7",
  save: "M3 2h8l2 2v10H3zM5 2v4h6V2M5 14v-4h6v4",
  fullscreen: "M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4",
};

export function icon(name: keyof typeof P | string, size = 16): SVGSVGElement {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add("icon");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", P[name] ?? P.plus!);
  path.setAttribute("fill", name === "play" || name === "pause" || name === "diamond" ? "currentColor" : "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.5");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  svg.append(path);
  return svg;
}

/** Icon button with an accessible label and a tooltip that names the shortcut. */
export function iconButton(name: string, label: string, onClick: (e: MouseEvent) => void, shortcut?: string, cls = "btn icon-btn"): HTMLButtonElement {
  const b = h("button", { class: cls, type: "button", "aria-label": label, "data-tip": shortcut ? `${label} (${shortcut})` : label, onclick: onClick }, icon(name));
  return b;
}

export function button(label: string, onClick: (e: MouseEvent) => void, cls = "btn", iconName?: string, tip?: string): HTMLButtonElement {
  return h("button", { class: cls, type: "button", onclick: onClick, "data-tip": tip }, iconName ? icon(iconName) : null, h("span", null, label));
}

// ── toasts ───────────────────────────────────────────────────────────

let toastHost: HTMLElement | null = null;

export type ToastKind = "info" | "success" | "error" | "warn";

export function toast(message: string, kind: ToastKind = "info", ms = 4200, action?: { label: string; run: () => void }): void {
  if (!toastHost) {
    toastHost = h("div", { class: "toasts", role: "status", "aria-live": "polite" });
    document.body.append(toastHost);
  }
  const el = h("div", { class: `toast toast-${kind}` }, h("span", null, message));
  if (action) el.append(h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => { action.run(); el.remove(); } }, action.label));
  el.append(h("button", { class: "toast-x", type: "button", "aria-label": "Dismiss", onclick: () => el.remove() }, icon("close", 12)));
  toastHost.append(el);
  setTimeout(() => el.classList.add("out"), ms);
  setTimeout(() => el.remove(), ms + 300);
}

// ── modal dialogs ────────────────────────────────────────────────────

export interface ModalHandle {
  el: HTMLElement;
  body: HTMLElement;
  footer: HTMLElement;
  close: () => void;
  onClose: (fn: () => void) => void;
}

let openModals = 0;
export const modalOpen = () => openModals > 0;

export function modal(title: string, opts: { wide?: boolean; closable?: boolean } = {}): ModalHandle {
  const closers: Array<() => void> = [];
  const backdrop = h("div", { class: "modal-backdrop" });
  const body = h("div", { class: "modal-body" });
  const footer = h("div", { class: "modal-footer" });
  const prevFocus = document.activeElement as HTMLElement | null;
  const close = () => {
    if (!backdrop.isConnected) return;
    backdrop.remove();
    openModals--;
    document.removeEventListener("keydown", onKey);
    closers.forEach((f) => f());
    prevFocus?.focus?.();
  };
  const onKey = (e: KeyboardEvent) => {
    // only the top-most modal reacts to Escape
    if (e.key === "Escape" && opts.closable !== false && backdrop === document.querySelector(".modal-backdrop:last-of-type")) {
      e.stopPropagation();
      close();
    }
  };
  const el = h(
    "div",
    { class: `modal${opts.wide ? " modal-wide" : ""}`, role: "dialog", "aria-modal": "true", "aria-label": title },
    h("div", { class: "modal-head" }, h("h2", null, title), opts.closable === false ? null : iconButton("close", "Close", close, "Esc", "btn icon-btn btn-ghost")),
    body,
    footer,
  );
  backdrop.append(el);
  backdrop.addEventListener("mousedown", (e) => {
    if (e.target === backdrop && opts.closable !== false) close();
  });
  document.body.append(backdrop);
  openModals++;
  document.addEventListener("keydown", onKey);
  queueMicrotask(() => (el.querySelector("input,select,textarea,button.btn-primary") as HTMLElement | null)?.focus());
  return { el, body, footer, close, onClose: (fn) => closers.push(fn) };
}

export function confirmDialog(title: string, message: string, buttons: Array<{ label: string; value: string; primary?: boolean; danger?: boolean }>): Promise<string | null> {
  return new Promise((resolve) => {
    const m = modal(title);
    let done = false;
    m.body.append(h("p", null, message));
    for (const b of buttons) {
      m.footer.append(
        h("button", { class: `btn${b.primary ? " btn-primary" : ""}${b.danger ? " btn-danger" : ""}`, type: "button", onclick: () => { done = true; resolve(b.value); m.close(); } }, b.label),
      );
    }
    m.onClose(() => !done && resolve(null));
  });
}

export function promptDialog(title: string, label: string, value = "", okLabel = "OK"): Promise<string | null> {
  return new Promise((resolve) => {
    const m = modal(title);
    let done = false;
    const input = h("input", { class: "input", type: "text", value, "aria-label": label });
    const submit = () => {
      done = true;
      resolve(input.value);
      m.close();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submit();
    });
    m.body.append(h("label", { class: "field-label" }, label), input);
    m.footer.append(h("button", { class: "btn", type: "button", onclick: () => m.close() }, "Cancel"), h("button", { class: "btn btn-primary", type: "button", onclick: submit }, okLabel));
    m.onClose(() => !done && resolve(null));
    queueMicrotask(() => {
      input.focus();
      input.select();
    });
  });
}

export function progressBar(): { el: HTMLElement; set: (p: number, label?: string) => void } {
  const fill = h("div", { class: "progress-fill" });
  const text = h("div", { class: "progress-label" });
  const el = h("div", { class: "progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100" }, h("div", { class: "progress-track" }, fill), text);
  return {
    el,
    set(p, label) {
      const pct = Math.round(Math.max(0, Math.min(1, p)) * 100);
      fill.style.width = `${pct}%`;
      el.setAttribute("aria-valuenow", String(pct));
      text.textContent = label ?? `${pct}%`;
    },
  };
}

export function isTyping(e: Event): boolean {
  const t = e.target as HTMLElement | null;
  if (!t) return false;
  const tag = t.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t.isContentEditable;
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

// ── tooltips ([data-tip]) ────────────────────────────────────────────

export function installTooltips(): void {
  const tip = h("div", { class: "tooltip", role: "tooltip" });
  document.body.append(tip);
  let timer = 0;
  let current: HTMLElement | null = null;
  const hide = () => {
    clearTimeout(timer);
    current = null;
    tip.classList.remove("show");
  };
  document.addEventListener("pointerover", (e) => {
    const el = (e.target as HTMLElement | null)?.closest?.("[data-tip]") as HTMLElement | null;
    if (el === current) return;
    hide();
    if (!el) return;
    current = el;
    timer = window.setTimeout(() => {
      const text = el.getAttribute("data-tip");
      if (!text || !el.isConnected) return;
      tip.textContent = text;
      const r = el.getBoundingClientRect();
      const tw = tip.offsetWidth;
      const th = tip.offsetHeight;
      let x = r.left + r.width / 2 - tw / 2;
      x = Math.max(6, Math.min(window.innerWidth - tw - 6, x));
      let y = r.bottom + 6;
      if (y + th > window.innerHeight - 4) y = r.top - th - 6;
      tip.style.left = `${x}px`;
      tip.style.top = `${y}px`;
      tip.classList.add("show");
    }, 450);
  });
  document.addEventListener("pointerdown", hide, true);
  document.addEventListener("keydown", hide, true);
}

/** Engine timestamps are unix seconds (float) or ISO strings. */
export function fmtDate(v: unknown): string {
  if (v === null || v === undefined || v === "") return "";
  const d = typeof v === "number" ? new Date(v < 1e12 ? v * 1000 : v) : new Date(String(v));
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString();
}
