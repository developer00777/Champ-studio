// Minimal Chrome DevTools Protocol helper for the UI audit (no npm dependencies; Node 22+).
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Chrome / Chromium binary: $CHROME, else the usual install location for this OS. */
function chromePath() {
  if (process.env.CHROME) return process.env.CHROME;
  if (process.platform === "win32") return "C:/Program Files/Google/Chrome/Application/chrome.exe";
  if (process.platform === "darwin") return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  return "google-chrome";
}

export async function launch(outDir, port = 9334) {
  const chrome = spawn(chromePath(), [
    "--headless=new", `--remote-debugging-port=${port}`, "--window-size=1440,900", `--user-data-dir=${outDir}/cdp-prof-${port}`,
    "--autoplay-policy=no-user-gesture-required", "about:blank",
  ], { stdio: "ignore" });
  let ws;
  for (let i = 0; i < 60 && !ws; i++) {
    await sleep(250);
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
      const page = list.find((t) => t.type === "page");
      if (page) ws = new WebSocket(page.webSocketDebuggerUrl);
    } catch {}
  }
  await new Promise((r) => (ws.onopen = r));
  let id = 0;
  const pending = new Map();
  const events = [];
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && pending.has(d.id)) {
      pending.get(d.id)(d);
      pending.delete(d.id);
    } else if (d.method) events.push(d);
  };
  const send = (method, params = {}) =>
    new Promise((r) => {
      const i = ++id;
      pending.set(i, r);
      ws.send(JSON.stringify({ id: i, method, params }));
    });
  const page = {
    send,
    events,
    async js(expr) {
      const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.result?.exceptionDetails) return { __error: r.result.exceptionDetails.exception?.description ?? r.result.exceptionDetails.text };
      return r.result?.result?.value;
    },
    async shot(file) {
      const r = await send("Page.captureScreenshot", { format: "png" });
      writeFileSync(file, Buffer.from(r.result.data, "base64"));
    },
    async size(w, h, mobile = false) {
      await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile });
    },
    async goto(url, wait = 2500) {
      await send("Page.navigate", { url });
      await sleep(wait);
    },
    async click(x, y, count = 1) {
      for (const type of ["mousePressed", "mouseReleased"]) await send("Input.dispatchMouseEvent", { type, x, y, button: "left", clickCount: count });
    },
    async key(key, code = key, vk = 0, mods = 0) {
      await send("Input.dispatchKeyEvent", { type: "keyDown", key, code, windowsVirtualKeyCode: vk, modifiers: mods });
      await send("Input.dispatchKeyEvent", { type: "keyUp", key, code, windowsVirtualKeyCode: vk, modifiers: mods });
    },
    /** Console errors, exceptions and failed requests since the last call. */
    drain() {
      const out = [];
      const reqs = new Map();
      for (const e of events.splice(0)) {
        if (e.method === "Runtime.exceptionThrown") out.push("EXCEPTION " + (e.params.exceptionDetails.exception?.description ?? e.params.exceptionDetails.text).split("\n").slice(0, 2).join(" | "));
        if (e.method === "Runtime.consoleAPICalled" && (e.params.type === "error" || e.params.type === "warning"))
          out.push(`CONSOLE.${e.params.type} ` + e.params.args.map((a) => a.value ?? a.description).join(" ").slice(0, 240));
        if (e.method === "Log.entryAdded" && e.params.entry.level === "error") out.push("LOG " + e.params.entry.text.slice(0, 200) + " " + (e.params.entry.url ?? ""));
        if (e.method === "Network.requestWillBeSent") reqs.set(e.params.requestId, e.params.request.method + " " + e.params.request.url);
        if (e.method === "Network.responseReceived" && e.params.response.status >= 400) out.push(`HTTP ${e.params.response.status} ${reqs.get(e.params.requestId) ?? e.params.response.url}`);
        if (e.method === "Network.loadingFailed" && !e.params.canceled) out.push(`NETFAIL ${e.params.errorText} ${reqs.get(e.params.requestId) ?? ""}`);
      }
      return out;
    },
    close() {
      ws.close();
      chrome.kill();
    },
  };
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Network.enable");
  await send("Log.enable");
  return page;
}

/** In-page visual heuristics: low-contrast text, clipped labels, page overflow, empty buttons, broken images. */
export const HEURISTICS = `(() => {
  const path = (el) => { const p = []; for (let e = el; e && e !== document.body && p.length < 4; e = e.parentElement) p.unshift(e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (e.classList.length ? '.' + [...e.classList].slice(0, 2).join('.') : '')); return p.join(' > '); };
  const rgb = (s) => { const m = /rgba?\\(([^)]+)\\)/.exec(s); if (!m) return null; const p = m[1].split(',').map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const bgOf = (el) => { for (let e = el; e; e = e.parentElement) { const s = getComputedStyle(e); if (s.backgroundImage && s.backgroundImage !== 'none') return null; const c = rgb(s.backgroundColor); if (c && c.a > 0.6) return c; } return rgb(getComputedStyle(document.body).backgroundColor); };
  const shown = (el) => { const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false; for (let e = el; e; e = e.parentElement) { const s = getComputedStyle(e); if (s.display === 'none' || s.visibility === 'hidden' || +s.opacity < 0.05) return false; } return r.bottom > 0 && r.top < innerHeight * 3; };
  const issues = [];
  const seen = new Set();
  const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (tw.nextNode()) {
    const t = tw.currentNode; const el = t.parentElement;
    if (!t.textContent.trim() || !el || seen.has(el) || ['SCRIPT','STYLE','OPTION','TEXTAREA'].includes(el.tagName)) continue;
    seen.add(el);
    if (!shown(el)) continue;
    const fg = rgb(getComputedStyle(el).color); const bg = bgOf(el);
    if (!fg || !bg) continue;
    const a = lum(fg), b = lum(bg); const cr = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    if (cr < 2.0 || fg.a < 0.3) issues.push({ kind: 'low-contrast', cr: +cr.toFixed(2), text: t.textContent.trim().slice(0, 40), at: path(el) });
  }
  for (const el of document.querySelectorAll('button, .btn, label, h1, h2, h3, .chip, .tab, .nav-item, .badge, th, .card-title')) {
    if (!shown(el)) continue;
    const s = getComputedStyle(el);
    if (el.scrollWidth > el.clientWidth + 3 && s.textOverflow !== 'ellipsis' && s.overflow !== 'visible') issues.push({ kind: 'clipped', text: el.textContent.trim().slice(0, 40), at: path(el), sw: el.scrollWidth, cw: el.clientWidth });
  }
  for (const b of document.querySelectorAll('button, a.btn, [role=button]')) {
    if (!shown(b)) continue;
    if (!b.textContent.trim() && !b.getAttribute('aria-label') && !b.title && !b.dataset.tip) issues.push({ kind: 'unlabelled-button', at: path(b) });
  }
  for (const img of document.images) if (shown(img) && img.complete && img.naturalWidth === 0 && img.src) issues.push({ kind: 'broken-img', src: img.src.slice(0, 120), at: path(img) });
  const offenders = [];
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.right > innerWidth + 4 && r.width > 0 && shown(el) && getComputedStyle(el).position !== 'fixed') {
      const p = el.parentElement; const pr = p && p.getBoundingClientRect();
      if (!pr || pr.right <= innerWidth + 4) offenders.push({ at: path(el), right: Math.round(r.right) });
    }
  }
  return { docW: document.documentElement.scrollWidth, vw: innerWidth, overflowX: document.documentElement.scrollWidth > innerWidth + 2, offenders: offenders.slice(0, 6), issues: issues.slice(0, 40) };
})()`;
