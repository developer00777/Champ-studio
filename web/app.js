/* Champ Studio — web UI. Buildless ES module, no dependencies. */

/* ── tiny DOM + fetch helpers ───────────────────────────────────── */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(c));
  }
  return node;
}

async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: {} };
  if (form) opts.body = form;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw new Error("Engine unreachable — is it running on this port?");
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (data.detail) detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch { /* non-JSON error body */ }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

/* toasts */
function toast(msg, kind = "info", ms = 4500) {
  const t = el("div", { class: `toast ${kind === "error" ? "err" : kind === "ok" ? "ok" : ""}` }, msg);
  $("#toast-root").append(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 350); }, ms);
}

/* localStorage prefs */
const PREFIX = "champstudio.";
const pref = (k, d) => {
  try { const v = localStorage.getItem(PREFIX + k); return v === null ? d : JSON.parse(v); }
  catch { return d; }
};
const setPref = (k, v) => { try { localStorage.setItem(PREFIX + k, JSON.stringify(v)); } catch { /* private mode */ } };

const fmtDate = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};
const shortModel = (m) => (m || "").split("/").pop() || m || "";

function download(asset) {
  const a = el("a", { href: asset.url, download: asset.filename || `champ-${asset.id}` });
  document.body.append(a); a.click(); a.remove();
}

/* ── modal system ───────────────────────────────────────────────── */

function openModal(title, bodyEl, { wide = false } = {}) {
  const close = () => { overlay.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const overlay = el("div", { class: "modal-overlay", onclick: (e) => { if (e.target === overlay) close(); } },
    el("div", { class: "modal", style: wide ? "width:min(1000px,100%)" : "" },
      el("div", { class: "modal-head" },
        el("h2", {}, title),
        el("button", { class: "modal-x", "aria-label": "Close", onclick: close }, "×")),
      el("div", { class: "modal-body" }, bodyEl)));
  document.addEventListener("keydown", onKey);
  $("#modal-root").append(overlay);
  return close;
}

/* library picker modal → resolves an Asset or null */
function pickFromLibrary({ kinds = ["image"] } = {}) {
  return new Promise((resolve) => {
    const grid = el("div", { class: "picker-grid" }, el("div", { class: "combo-empty" }, "Loading…"));
    const close = openModal("Pick from Library", grid, { wide: true });
    const done = (asset) => { close(); resolve(asset); };
    api(`/api/assets?limit=200`).then(({ assets }) => {
      const usable = assets.filter((a) => kinds.includes(a.kind));
      grid.replaceChildren(...(usable.length
        ? usable.map((a) => el("button", { class: "pick", title: a.prompt || a.filename || a.id, onclick: () => done(a) },
            a.kind === "audio"
              ? el("div", { class: "pick-audio" }, el("span", {}, "♪"), el("small", {}, (a.prompt || a.filename || a.id).slice(0, 60)))
              : el("img", { src: a.url, alt: "", loading: "lazy" })))
        : [el("div", { class: "combo-empty" }, "No matching assets yet — generate something in Create first.")]));
    }).catch((e) => grid.replaceChildren(el("div", { class: "combo-empty" }, e.message)));
    // resolve(null) when dismissed without picking
    const observer = new MutationObserver(() => {
      if (!document.body.contains(grid)) { observer.disconnect(); resolve(null); }
    });
    observer.observe($("#modal-root"), { childList: true });
  });
}

/* ── searchable model combo ─────────────────────────────────────── */

function modelCombo(slot, { endpoint, storeKey, fallback, onChange }) {
  let models = [];
  let value = pref(storeKey, fallback.id);
  let valueName = pref(storeKey + ".name", fallback.name);
  let pop = null;

  const nameSpan = el("span", { class: "name" }, valueName);
  const btn = el("button", { class: "sel combo-btn", type: "button", onclick: toggle },
    nameSpan, el("span", { class: "caret" }, "▼"));
  const root = el("div", { class: "combo" }, btn);
  slot.append(root);

  function select(m) {
    value = m.id; valueName = m.name || m.id;
    setPref(storeKey, value); setPref(storeKey + ".name", valueName);
    nameSpan.textContent = valueName;
    closePop();
    onChange?.(m);
  }
  function closePop() { pop?.remove(); pop = null; document.removeEventListener("click", onDocClick, true); }
  function onDocClick(e) { if (pop && !root.contains(e.target)) closePop(); }

  function renderList(listEl, filter) {
    const q = filter.trim().toLowerCase();
    const hits = models.filter((m) => !q || `${m.name} ${m.id} ${m.vendor}`.toLowerCase().includes(q));
    const groups = new Map();
    for (const m of hits) {
      const g = m.vendor || m.provider || "other";
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(m);
    }
    listEl.replaceChildren();
    if (!hits.length) { listEl.append(el("div", { class: "combo-empty" }, "No models match.")); return; }
    for (const [vendor, list] of groups) {
      listEl.append(el("div", { class: "combo-group" }, vendor));
      for (const m of list) {
        listEl.append(el("button", { class: `combo-opt${m.id === value ? " sel-now" : ""}`, onclick: () => select(m) },
          m.name || m.id, el("small", {}, `${m.id} · ${m.provider}`)));
      }
    }
  }

  function toggle() {
    if (pop) { closePop(); return; }
    const listEl = el("div", { class: "combo-list" });
    const search = el("input", { class: "sel", placeholder: "Filter models…", oninput: () => renderList(listEl, search.value) });
    pop = el("div", { class: "combo-pop" }, search, listEl);
    root.append(pop);
    renderList(listEl, "");
    search.focus();
    setTimeout(() => document.addEventListener("click", onDocClick, true));
  }

  (async () => {
    try {
      const list = await api(endpoint);
      models = Array.isArray(list) && list.length ? list : [fallback];
    } catch { models = [fallback]; }
    const current = models.find((m) => m.id === value);
    if (current) onChange?.(current); else select(models[0]);
  })();

  return {
    get value() { return value; },
    get model() { return models.find((m) => m.id === value) || null; },
  };
}

/* ── chips + switch helpers ─────────────────────────────────────── */

function chipGroup(container, options, storeKey, def) {
  let value = pref(storeKey, def);
  const chips = options.map((o) => el("button", {
    class: `chip${o.value === value ? " active" : ""}`, type: "button",
    onclick: () => {
      value = o.value; setPref(storeKey, value);
      chips.forEach((c, i) => c.classList.toggle("active", options[i].value === value));
    },
  }, o.label));
  container.append(...chips);
  return { get value() { return value; } };
}

function bindSwitch(elWrap, storeKey, def = false) {
  let on = pref(storeKey, def);
  const paint = () => elWrap.setAttribute("aria-checked", String(on));
  elWrap.addEventListener("click", () => { on = !on; setPref(storeKey, on); paint(); });
  paint();
  return { get value() { return on; } };
}

function autoGrow(ta) {
  const fit = () => {
    ta.style.height = "auto";
    if (ta.scrollHeight) ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
  };
  ta.addEventListener("input", fit);
  ta.addEventListener("focus", fit);
  fit();
}

/* ── routing ────────────────────────────────────────────────────── */

const onEnter = {}; // view -> fn
function show(view) {
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  if (location.hash !== `#/${view}`) history.replaceState(null, "", `#/${view}`);
  onEnter[view]?.();
}
$("#nav").addEventListener("click", (e) => {
  const btn = e.target.closest(".nav-item");
  if (btn) show(btn.dataset.view);
});
document.body.addEventListener("click", (e) => {
  const go = e.target.closest("[data-goto]");
  if (go) show(go.dataset.goto);
});
window.addEventListener("hashchange", () => {
  const v = location.hash.replace(/^#\//, "");
  if ($(`#view-${v}`)) show(v);
});

/* ── health / status ────────────────────────────────────────────── */

async function pollHealth() {
  const dot = $("#status-dot"), text = $("#status-text"), ver = $("#engine-version");
  try {
    const h = await api("/api/health");
    dot.className = "dot ok";
    const keys = [];
    if (h.keys?.openrouter) keys.push("OpenRouter");
    if (h.keys?.muapi) keys.push("MuAPI");
    text.textContent = keys.length ? `Online · ${keys.join(" + ")}` : "Online · no keys set";
    $("#engine-status").title = keys.length ? `Engine ok — keys: ${keys.join(", ")}` : "Engine ok — add API keys in Settings";
    ver.textContent = `${h.name || "engine"} v${h.version || "?"}`;
    $("#settings-engine").textContent = `${h.name || "Champ Studio Engine"} · version ${h.version || "?"} · keys: OpenRouter ${h.keys?.openrouter ? "✓" : "—"}, MuAPI ${h.keys?.muapi ? "✓" : "—"}`;
  } catch {
    dot.className = "dot err";
    text.textContent = "Engine unreachable";
    $("#engine-status").title = "Cannot reach /api/health";
  }
}
pollHealth();
setInterval(pollHealth, 10_000);

/* ── shared asset card ──────────────────────────────────────────── */

function mediaFor(asset, { controls = false } = {}) {
  if (asset.kind === "video") {
    return el("video", { class: "media", src: asset.url, controls, muted: !controls, playsinline: true, preload: "metadata" });
  }
  if (asset.kind === "audio") {
    return el("div", { class: "media audio-tile" },
      el("span", { class: "audio-glyph" }, "♪"),
      el("audio", { src: asset.url, controls: true, preload: "metadata", onclick: (e) => e.stopPropagation() }));
  }
  return el("img", { class: "media", src: asset.url, alt: asset.prompt || "asset", loading: "lazy" });
}

function assetCard(asset, { onDelete } = {}) {
  const actions = [];
  if (asset.kind === "image" || asset.kind === "vector") {
    actions.push(el("button", { class: "btn sm", onclick: () => sendToAnimate(asset) }, "Animate"));
    actions.push(el("button", { class: "btn sm", onclick: () => sendToLab(asset) }, "Lab"));
  }
  if (asset.kind === "audio" || asset.kind === "image") {
    actions.push(el("button", { class: "btn sm", onclick: () => sendToLipSync(asset) }, "Lip Sync"));
  }
  actions.push(el("button", { class: "btn sm", onclick: () => download(asset) }, "Download"));
  actions.push(el("button", {
    class: "btn sm danger",
    onclick: async (e) => {
      e.stopPropagation();
      try {
        await api(`/api/assets/${asset.id}`, { method: "DELETE" });
        card.remove();
        toast("Asset deleted", "ok");
        onDelete?.(asset);
      } catch (err) { toast(err.message, "error"); }
    },
  }, "Delete"));

  const media = mediaFor(asset);
  media.addEventListener("click", () => openDetail(asset.id));
  const card = el("div", { class: "asset-card" },
    media,
    el("div", { class: "card-actions" }, actions),
    el("div", { class: "meta", title: asset.prompt || "" },
      el("span", { class: `badge ${asset.kind}` }, asset.kind),
      el("span", { class: "model" }, asset.tool || shortModel(asset.model) || asset.filename || "")));
  return card;
}

/* detail modal with lineage */
async function openDetail(assetId) {
  let asset;
  try { asset = await api(`/api/assets/${assetId}`); }
  catch (e) { toast(e.message, "error"); return; }

  const lineage = asset.lineage || [];
  const dl = el("dl", {},
    el("dt", {}, "Kind"), el("dd", {}, asset.kind),
    el("dt", {}, "Source"), el("dd", {}, asset.tool ? `tool · ${asset.tool}` : asset.model || "upload"),
    asset.provider ? [el("dt", {}, "Provider"), el("dd", {}, asset.provider)] : [],
    el("dt", {}, "File"), el("dd", {}, `${asset.filename || asset.id} · ${asset.mime || ""}`),
    asset.cost ? [el("dt", {}, "Cost"), el("dd", {}, `$${Number(asset.cost).toFixed(4)}`)] : [],
    el("dt", {}, "Created"), el("dd", {}, fmtDate(asset.created_at)));

  const canDerive = asset.kind === "image" || asset.kind === "vector";
  const actions = el("div", { class: "detail-actions" },
    canDerive && el("button", { class: "btn ember", onclick: () => { close(); sendToAnimate(asset); } }, "Animate"),
    canDerive && el("button", { class: "btn", onclick: () => { close(); sendToLab(asset); } }, "Open in Lab"),
    (asset.kind === "audio" || asset.kind === "image") && el("button", { class: "btn", onclick: () => { close(); sendToLipSync(asset); } }, "Lip Sync"),
    el("button", { class: "btn", onclick: () => download(asset) }, "Download"),
    el("button", {
      class: "btn danger",
      onclick: async () => {
        try {
          await api(`/api/assets/${asset.id}`, { method: "DELETE" });
          toast("Asset deleted", "ok"); close(); library.refresh();
        } catch (e) { toast(e.message, "error"); }
      },
    }, "Delete"));

  const body = el("div", { class: "detail-wrap" },
    el("div", { class: "detail-media" }, mediaFor(asset, { controls: true })),
    el("div", { class: "detail-side" },
      asset.prompt ? el("div", { class: "detail-prompt" }, asset.prompt) : [],
      dl,
      lineage.length > 1 ? [
        el("h3", { style: "font-size:13px;margin:0 0 8px" }, "Lineage"),
        el("div", { class: "lineage" }, lineage.flatMap((a, i) => [
          i ? el("span", { class: "arrow" }, "→") : [],
          el("button", {
            class: `crumb${a.id === asset.id ? " head" : ""}`,
            onclick: a.id === asset.id ? null : () => { close(); openDetail(a.id); },
          }, a.tool || shortModel(a.model) || a.kind),
        ])),
      ] : [],
      actions));
  const close = openModal(asset.prompt ? asset.prompt.slice(0, 64) + (asset.prompt.length > 64 ? "…" : "") : "Asset", body, { wide: true });
}

/* ── CREATE tabs ────────────────────────────────────────────────── */

const onTab = {}; // tab name -> fn, run when the tab becomes visible
const createTabs = (() => {
  const tabs = $$("#create-tabs .tab");
  const panes = $$("#view-create .tab-pane");
  function activate(name) {
    if (!tabs.some((t) => t.dataset.tab === name)) name = "image";
    tabs.forEach((t) => {
      const on = t.dataset.tab === name;
      t.classList.toggle("active", on);
      t.setAttribute("aria-selected", String(on));
    });
    panes.forEach((p) => { p.hidden = p.dataset.pane !== name; });
    setPref("create.tab", name);
    onTab[name]?.();
  }
  tabs.forEach((t) => t.addEventListener("click", () => activate(t.dataset.tab)));
  document.body.addEventListener("click", (e) => {
    const go = e.target.closest("[data-create-tab]");
    if (go) { show("create"); activate(go.dataset.createTab); }
  });
  activate(pref("create.tab", "image"));
  onEnter.create = () => onTab[pref("create.tab", "image")]?.();
  return { activate };
})();

/* ── CREATE view ────────────────────────────────────────────────── */

const create = (() => {
  const promptEl = $("#create-prompt");
  const goBtn = $("#create-go");
  const results = $("#create-results");
  const refsStrip = $("#create-refs");
  const emptyEl = $("#create-empty");
  autoGrow(promptEl);

  const model = modelCombo($("#create-model-slot"), {
    endpoint: "/api/models/images", storeKey: "create.model",
    fallback: { id: "google/gemini-2.5-flash-image", name: "Gemini 2.5 Flash Image", provider: "openrouter", vendor: "google" },
  });
  const aspect = chipGroup($("#create-aspect"),
    ["1:1", "16:9", "9:16", "4:3", "3:4"].map((v) => ({ value: v, label: v })), "create.aspect", "1:1");
  const transparent = bindSwitch($("#create-transparent"), "create.transparent");
  const resSel = $("#create-res"), countSel = $("#create-count"), fmtSel = $("#create-format");
  resSel.value = pref("create.res", ""); countSel.value = pref("create.count", "1"); fmtSel.value = pref("create.format", "");
  resSel.onchange = () => setPref("create.res", resSel.value);
  countSel.onchange = () => setPref("create.count", countSel.value);
  fmtSel.onchange = () => setPref("create.format", fmtSel.value);

  let refs = []; // Asset[]
  function paintRefs() {
    refsStrip.hidden = !refs.length;
    refsStrip.replaceChildren(...refs.map((a, i) => el("div", { class: "ref-item", title: a.prompt || a.filename || "" },
      el("img", { src: a.url, alt: "" }),
      el("button", { class: "rm", "aria-label": "Remove", onclick: () => { refs.splice(i, 1); paintRefs(); } }, "×"))));
  }
  $("#create-ref-btn").addEventListener("click", async () => {
    const a = await pickFromLibrary({ kinds: ["image", "vector"] });
    if (a && !refs.some((r) => r.id === a.id)) { refs.push(a); paintRefs(); }
  });

  async function generate() {
    const prompt = promptEl.value.trim();
    if (!prompt) { toast("Type a prompt first.", "error"); promptEl.focus(); return; }
    const n = Number(countSel.value) || 1;
    emptyEl.hidden = true;
    const shimmers = Array.from({ length: n }, () =>
      el("div", { class: "shimmer-card" }, el("div", { class: "shimmer" }), el("div", { class: "shimmer bar" })));
    results.prepend(...shimmers);
    goBtn.disabled = true; goBtn.classList.add("busy");
    try {
      const payload = {
        prompt, model: model.value, n,
        aspect_ratio: aspect.value,
        input_asset_ids: refs.map((r) => r.id),
      };
      if (resSel.value) payload.resolution = resSel.value;
      if (fmtSel.value) payload.output_format = fmtSel.value;
      if (transparent.value) payload.background = "transparent";
      const { assets } = await api("/api/images", { method: "POST", body: payload });
      shimmers.forEach((s) => s.remove());
      if (!assets?.length) { toast("The model returned no images.", "error"); return; }
      const cards = assets.map((a) => assetCard(a));
      results.prepend(...cards);
      toast(`Generated ${assets.length} image${assets.length > 1 ? "s" : ""}`, "ok");
    } catch (e) {
      shimmers.forEach((s) => s.remove());
      if (!results.children.length) emptyEl.hidden = false;
      toast(e.message, "error", 7000);
    } finally {
      goBtn.disabled = false; goBtn.classList.remove("busy");
    }
  }
  goBtn.addEventListener("click", generate);
  promptEl.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); generate(); }
  });
  return {};
})();

/* ── VOICE pane (text-to-speech) ────────────────────────────────── */

const voice = (() => {
  const textEl = $("#audio-text"), goBtn = $("#audio-go");
  const results = $("#audio-results"), emptyEl = $("#audio-empty");
  const voiceSel = $("#audio-voice"), speedEl = $("#audio-speed");
  autoGrow(textEl);
  speedEl.value = pref("audio.speed", 1);
  speedEl.onchange = () => setPref("audio.speed", Number(speedEl.value));

  function paintVoices(m) {
    const voices = m?.voices || [];
    const remembered = pref(`audio.voice.${m?.id}`, voices[0] || "");
    voiceSel.replaceChildren(...(voices.length
      ? voices.map((v) => el("option", { value: v, selected: v === remembered }, v))
      : [el("option", { value: "" }, "Provider default")]));
    voiceSel.disabled = !voices.length;
  }
  const model = modelCombo($("#audio-model-slot"), {
    endpoint: "/api/models/speech", storeKey: "audio.model",
    fallback: {
      id: "google/gemini-3.8-flash-tts", name: "Gemini 3.8 Flash TTS", provider: "openrouter", vendor: "google",
      voices: ["Kore", "Puck", "Zephyr", "Charon", "Fenrir", "Leda"],
    },
    onChange: paintVoices,
  });
  voiceSel.onchange = () => setPref(`audio.voice.${model.value}`, voiceSel.value);

  async function synthesize() {
    const text = textEl.value.trim();
    if (!text) { toast("Paste a script first.", "error"); textEl.focus(); return; }
    emptyEl.hidden = true;
    const shimmer = el("div", { class: "shimmer-card" }, el("div", { class: "shimmer" }), el("div", { class: "shimmer bar" }));
    results.prepend(shimmer);
    goBtn.disabled = true; goBtn.classList.add("busy");
    try {
      const payload = { text, model: model.value };
      if (voiceSel.value) payload.voice = voiceSel.value;
      const speed = Number(speedEl.value);
      if (speed && speed !== 1) payload.speed = speed;
      const { assets } = await api("/api/audio", { method: "POST", body: payload });
      shimmer.remove();
      results.prepend(...assets.map((a) => assetCard(a)));
      toast("Voiceover ready", "ok");
    } catch (e) {
      shimmer.remove();
      if (!results.children.length) emptyEl.hidden = false;
      toast(e.message, "error", 7000);
    } finally {
      goBtn.disabled = false; goBtn.classList.remove("busy");
    }
  }
  goBtn.addEventListener("click", synthesize);
  textEl.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); synthesize(); }
  });
  return {};
})();

/* ── LIP SYNC pane (portrait + voice track → talking head) ──────── */

const lipsync = (() => {
  const goBtn = $("#ls-go"), list = $("#ls-jobs"), emptyEl = $("#ls-empty");
  const imgPrev = $("#ls-image-preview"), audPrev = $("#ls-audio-preview");
  const aspect = chipGroup($("#ls-aspect"),
    ["9:16", "16:9", "1:1"].map((v) => ({ value: v, label: v })), "lipsync.aspect", "9:16");
  const resSel = $("#ls-res");
  resSel.value = pref("lipsync.res", "720p");
  resSel.onchange = () => setPref("lipsync.res", resSel.value);
  let image = null, audio = null; // Asset | null

  function paint() {
    imgPrev.replaceChildren(image
      ? el("img", { src: image.url, alt: "Portrait" })
      : el("span", { class: "empty-mark" }, "☺"));
    audPrev.replaceChildren(audio
      ? el("audio", { src: audio.url, controls: true, preload: "metadata" })
      : el("span", { class: "empty-mark" }, "♪"));
    goBtn.disabled = !(image && audio);
  }
  function setImage(a) { image = a; paint(); }
  function setAudio(a) { audio = a; paint(); }

  async function uploadTo(setter, file, kind) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      toast(`Uploading ${file.name}…`);
      const asset = await api("/api/assets/upload", { method: "POST", form });
      if (asset.kind !== kind) { toast(`That file is ${asset.kind}, not ${kind}.`, "error"); return; }
      setter(asset);
      toast("Uploaded", "ok");
    } catch (e) { toast(e.message, "error"); }
  }
  $("#ls-image-pick").addEventListener("click", async () => { const a = await pickFromLibrary({ kinds: ["image"] }); if (a) setImage(a); });
  $("#ls-audio-pick").addEventListener("click", async () => { const a = await pickFromLibrary({ kinds: ["audio"] }); if (a) setAudio(a); });
  $("#ls-image-upload").addEventListener("click", () => $("#ls-image-file").click());
  $("#ls-audio-upload").addEventListener("click", () => $("#ls-audio-file").click());
  $("#ls-image-file").addEventListener("change", (e) => { uploadTo(setImage, e.target.files[0], "image"); e.target.value = ""; });
  $("#ls-audio-file").addEventListener("change", (e) => { uploadTo(setAudio, e.target.files[0], "audio"); e.target.value = ""; });

  let pollTimer = null;
  async function refreshJobs() {
    let jobs;
    try { ({ jobs } = await api("/api/videos/jobs")); }
    catch { return; }
    jobs = (jobs || []).filter((j) => j.kind === "lipsync");
    emptyEl.hidden = jobs.length > 0;
    list.replaceChildren(...jobs.map(jobCard));
    const active = jobs.some((j) => j.status === "pending" || j.status === "in_progress");
    if (active && !pollTimer) pollTimer = setInterval(refreshJobs, 3000);
    if (!active && pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  async function submit() {
    if (!(image && audio)) { toast("Pick a portrait and a voice track first.", "error"); return; }
    goBtn.disabled = true; goBtn.classList.add("busy");
    try {
      await api("/api/lipsync", { method: "POST", body: {
        image_asset_id: image.id, audio_asset_id: audio.id,
        aspect_ratio: aspect.value, resolution: resSel.value,
      } });
      toast("Lip sync job queued", "ok");
      refreshJobs();
    } catch (e) {
      toast(e.message, "error", 7000);
    } finally {
      goBtn.classList.remove("busy"); paint();
    }
  }
  goBtn.addEventListener("click", submit);
  onTab.lipsync = refreshJobs;
  paint();
  return { setImage, setAudio, refreshJobs };
})();

function sendToLipSync(asset) {
  if (asset.kind === "audio") lipsync.setAudio(asset); else lipsync.setImage(asset);
  show("create");
  createTabs.activate("lipsync");
}

/* ── shared job card (Animate + Lip Sync) ───────────────────────── */

function jobCard(job) {
  const st = job.status || "pending";
  const media = st === "completed" && job.asset
    ? el("div", { class: "job-media" }, el("video", { src: job.asset.url, controls: true, playsinline: true, preload: "metadata" }))
    : el("div", { class: "job-media" }, el("div", { class: "job-wait" },
        st === "failed" ? el("span", { style: "font-size:22px" }, "✕") : el("div", { class: "pulse-ring" }),
        st === "failed" ? "render failed" : st === "pending" ? "queued…" : "rendering…"));
  return el("div", { class: "job-card", dataset: { job: job.id } },
    media,
    el("div", { class: "job-info" },
      el("div", { class: "job-top" },
        el("span", { class: `status-pill ${st}` }, st.replace("_", " ")),
        el("span", { class: "job-model" }, shortModel(job.model)),
        el("span", { class: "job-date" }, fmtDate(job.created_at))),
      el("p", { class: "prompt", title: job.prompt || "" }, job.prompt || (job.kind === "lipsync" ? "Lip sync" : "")),
      job.error ? el("div", { class: "job-error" }, job.error) : [],
      st === "completed" && job.asset ? el("div", { class: "job-actions" },
        el("button", { class: "btn sm", onclick: () => download(job.asset) }, "Download"),
        el("button", { class: "btn sm", onclick: () => openDetail(job.asset.id) }, "Details")) : []));
}

/* ── ANIMATE view ───────────────────────────────────────────────── */

const animate = (() => {
  const promptEl = $("#animate-prompt");
  const goBtn = $("#animate-go");
  const srcStrip = $("#animate-src");
  const list = $("#jobs-list");
  const emptyEl = $("#jobs-empty");
  autoGrow(promptEl);

  const model = modelCombo($("#animate-model-slot"), {
    endpoint: "/api/models/videos", storeKey: "animate.model",
    fallback: { id: "google/veo-3.1", name: "Veo 3.1", provider: "openrouter", vendor: "google" },
  });
  const aspect = chipGroup($("#animate-aspect"),
    ["16:9", "9:16", "1:1", "4:3", "3:4"].map((v) => ({ value: v, label: v })), "animate.aspect", "16:9");
  const audio = bindSwitch($("#animate-audio"), "animate.audio");
  const durEl = $("#animate-duration"), resSel = $("#animate-res");
  durEl.value = pref("animate.duration", 5); resSel.value = pref("animate.res", "");
  durEl.onchange = () => setPref("animate.duration", Number(durEl.value));
  resSel.onchange = () => setPref("animate.res", resSel.value);

  let source = null; // Asset | null
  function setSource(asset) {
    source = asset;
    srcStrip.hidden = !asset;
    srcStrip.replaceChildren(asset ? el("div", { class: "ref-item", title: "First frame" },
      el("img", { src: asset.url, alt: "First frame" }),
      el("button", { class: "rm", "aria-label": "Remove", onclick: () => setSource(null) }, "×")) : []);
    $("#animate-src-btn").textContent = asset ? "Change source image" : "+ From library";
  }
  $("#animate-src-btn").addEventListener("click", async () => {
    const a = await pickFromLibrary({ kinds: ["image"] });
    if (a) setSource(a);
  });

  /* jobs */
  let pollTimer = null;
  async function refreshJobs() {
    let jobs;
    try { ({ jobs } = await api("/api/videos/jobs")); }
    catch { return; }
    jobs = (jobs || []).filter((j) => j.kind !== "lipsync");
    emptyEl.hidden = jobs.length > 0;
    list.replaceChildren(...jobs.map(jobCard));
    const active = jobs.some((j) => j.status === "pending" || j.status === "in_progress");
    if (active && !pollTimer) pollTimer = setInterval(refreshJobs, 3000);
    if (!active && pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  async function submit() {
    const prompt = promptEl.value.trim();
    if (!prompt) { toast("Describe the motion first.", "error"); promptEl.focus(); return; }
    goBtn.disabled = true; goBtn.classList.add("busy");
    try {
      const payload = { prompt, model: model.value, aspect_ratio: aspect.value };
      const dur = Number(durEl.value);
      if (dur > 0) payload.duration = dur;
      if (resSel.value) payload.resolution = resSel.value;
      if (audio.value) payload.generate_audio = true;
      if (source) payload.image_asset_id = source.id;
      await api("/api/videos", { method: "POST", body: payload });
      toast("Video job queued", "ok");
      refreshJobs();
    } catch (e) {
      toast(e.message, "error", 7000);
    } finally {
      goBtn.disabled = false; goBtn.classList.remove("busy");
    }
  }
  goBtn.addEventListener("click", submit);
  promptEl.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); submit(); }
  });

  onEnter.animate = refreshJobs;
  return { setSource };
})();

function sendToAnimate(asset) { animate.setSource(asset); show("animate"); $("#animate-prompt").focus(); }

/* ── DERIVATIVE LAB view ────────────────────────────────────────── */

const lab = (() => {
  const dropZone = $("#lab-drop");
  const currentEl = $("#lab-current");
  const toolsWrap = $("#lab-tools-wrap");
  const toolsGrid = $("#lab-tools");
  const resultsStrip = $("#lab-results");
  const lineageEl = $("#lab-lineage");
  let source = null;
  let toolsLoaded = false;

  async function paintLineage(asset) {
    lineageEl.replaceChildren(el("span", {}, "…"));
    let lineage = [asset];
    try {
      const detail = await api(`/api/assets/${asset.id}`);
      if (detail.lineage?.length) lineage = detail.lineage;
    } catch { /* lineage optional */ }
    lineageEl.replaceChildren(...lineage.flatMap((a, i) => [
      i ? el("span", { class: "arrow" }, "→") : [],
      el("button", {
        class: `crumb${a.id === asset.id ? " head" : ""}`,
        title: a.prompt || a.filename || a.id,
        onclick: a.id === asset.id ? null : () => setSource(a),
      }, a.tool || shortModel(a.model) || a.kind),
    ]));
  }

  function setSource(asset) {
    source = asset;
    dropZone.hidden = !!asset;
    currentEl.hidden = !asset;
    toolsWrap.hidden = !asset;
    if (asset) {
      $("#lab-thumb").src = asset.url;
      paintLineage(asset);
      if (!toolsLoaded) loadTools();
    }
  }

  function paramInput(name, spec) {
    if (spec.type === "select") {
      const s = el("select", { class: "sel" }, spec.options.map((o) => el("option", { value: o, selected: o === spec.default }, o)));
      return { node: s, read: () => s.value };
    }
    if (spec.type === "int") {
      const i = el("input", { class: "sel", type: "number", min: spec.min, max: spec.max, value: spec.default });
      return { node: i, read: () => Number(i.value) };
    }
    if (spec.type === "bool") {
      const sw = el("button", { class: "switch", role: "switch", "aria-checked": String(!!spec.default), type: "button" }, el("span"));
      sw.addEventListener("click", () => sw.setAttribute("aria-checked", String(sw.getAttribute("aria-checked") !== "true")));
      return { node: sw, read: () => sw.getAttribute("aria-checked") === "true" };
    }
    const t = el("input", { class: "sel", value: spec.default ?? "" });
    return { node: t, read: () => t.value };
  }

  function toolCard(tool) {
    const readers = {};
    const paramsEl = el("div", { class: "tool-params" });
    for (const [name, spec] of Object.entries(tool.params || {})) {
      const { node, read } = paramInput(name, spec);
      readers[name] = read;
      paramsEl.append(el("div", { class: "tp-row" }, el("label", {}, name), node));
    }
    const runBtn = el("button", {
      class: "btn ember sm",
      onclick: async () => {
        if (!source) return;
        runBtn.disabled = true; runBtn.classList.add("busy");
        try {
          const params = Object.fromEntries(Object.entries(readers).map(([k, r]) => [k, r()]));
          const { assets } = await api(`/api/transform/${tool.id}`, { method: "POST", body: { asset_id: source.id, params } });
          if (!assets?.length) { toast("Transform returned nothing.", "error"); return; }
          $("#lab-results-title").hidden = false;
          for (const a of assets) resultsStrip.prepend(resultCard(a));
          toast(`${tool.label} done`, "ok");
        } catch (e) { toast(e.message, "error", 7000); }
        finally { runBtn.disabled = false; runBtn.classList.remove("busy"); }
      },
    }, "Run");
    return el("div", { class: "tool-card" },
      el("h3", {}, tool.label),
      el("p", { class: "desc" }, tool.description || ""),
      paramsEl, runBtn);
  }

  function resultCard(asset) {
    const media = mediaFor(asset);
    media.addEventListener("click", () => openDetail(asset.id));
    return el("div", { class: "asset-card" },
      media,
      el("div", { class: "card-actions" },
        el("button", { class: "btn sm ember", onclick: () => setSource(asset) }, "Use as source"),
        el("button", { class: "btn sm", onclick: () => download(asset) }, "Download")),
      el("div", { class: "meta" },
        el("span", { class: `badge ${asset.kind}` }, asset.kind),
        el("span", { class: "model" }, asset.tool || "")));
  }

  async function loadTools() {
    toolsLoaded = true;
    toolsGrid.replaceChildren(el("div", { class: "combo-empty" }, "Loading tools…"));
    try {
      const { tools } = await api("/api/transform");
      toolsGrid.replaceChildren(...(tools || []).map(toolCard));
    } catch (e) {
      toolsLoaded = false;
      toolsGrid.replaceChildren(el("div", { class: "combo-empty" }, `Couldn't load tools: ${e.message}`));
    }
  }

  async function uploadFile(file) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      toast(`Uploading ${file.name}…`);
      const asset = await api("/api/assets/upload", { method: "POST", form });
      setSource(asset.asset || asset);
      toast("Uploaded", "ok");
    } catch (e) { toast(e.message, "error"); }
  }

  $("#lab-pick").addEventListener("click", async () => {
    const a = await pickFromLibrary({ kinds: ["image", "vector"] });
    if (a) setSource(a);
  });
  $("#lab-upload").addEventListener("click", () => $("#lab-file").click());
  $("#lab-file").addEventListener("change", (e) => { uploadFile(e.target.files[0]); e.target.value = ""; });
  $("#lab-swap").addEventListener("click", () => setSource(null));

  for (const evt of ["dragenter", "dragover"]) {
    dropZone.addEventListener(evt, (e) => { e.preventDefault(); dropZone.classList.add("over"); });
  }
  for (const evt of ["dragleave", "drop"]) {
    dropZone.addEventListener(evt, (e) => { e.preventDefault(); dropZone.classList.remove("over"); });
  }
  dropZone.addEventListener("drop", (e) => uploadFile(e.dataTransfer.files[0]));

  return { setSource };
})();

function sendToLab(asset) { lab.setSource(asset); show("lab"); }

/* ── LIBRARY view ───────────────────────────────────────────────── */

const library = (() => {
  const grid = $("#library-grid");
  const emptyEl = $("#library-empty");
  const kindFilter = chipGroup($("#library-filters"), [
    { value: "", label: "All" },
    { value: "image", label: "Images" },
    { value: "video", label: "Videos" },
    { value: "audio", label: "Audio" },
    { value: "vector", label: "Vectors" },
    { value: "data", label: "Data" },
  ], "library.kind", "");
  $("#library-filters").addEventListener("click", (e) => { if (e.target.closest(".chip")) refresh(); });

  let loading = false;
  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const kind = kindFilter.value;
      const { assets } = await api(`/api/assets?limit=120${kind ? `&kind=${kind}` : ""}`);
      grid.replaceChildren(...(assets || []).map((a) => assetCard(a, { onDelete: refresh })));
      emptyEl.hidden = (assets || []).length > 0;
    } catch (e) {
      toast(e.message, "error");
    } finally { loading = false; }
  }
  onEnter.library = refresh;
  return { refresh };
})();

/* ── SETTINGS view ──────────────────────────────────────────────── */

(() => {
  const fields = { openrouter: $("#key-openrouter"), muapi: $("#key-muapi") };
  const notes = { openrouter: $("#note-openrouter"), muapi: $("#note-muapi") };
  const changed = new Set();
  for (const [k, input] of Object.entries(fields)) {
    input.addEventListener("input", () => changed.add(k));
  }

  async function load() {
    try {
      const s = await api("/api/settings");
      for (const k of Object.keys(fields)) {
        const masked = s.keys?.[k];
        const src = s.key_sources?.[k];
        fields[k].value = "";
        changed.delete(k);
        fields[k].placeholder = masked || (k === "openrouter" ? "sk-or-…" : "mu-…");
        if (src === "env") {
          fields[k].disabled = true;
          notes[k].textContent = "Set via environment — edit your env to change it.";
          notes[k].className = "key-note env";
        } else {
          fields[k].disabled = false;
          notes[k].textContent = masked ? "A key is saved. Type to replace, or clear + save to remove." : "No key set.";
          notes[k].className = "key-note";
        }
      }
    } catch (e) { toast(`Couldn't load settings: ${e.message}`, "error"); }
  }

  $("#settings-save").addEventListener("click", async () => {
    const keys = {};
    for (const k of changed) if (!fields[k].disabled) keys[k] = fields[k].value.trim();
    if (!Object.keys(keys).length) { toast("Nothing changed."); return; }
    const btn = $("#settings-save");
    btn.disabled = true; btn.classList.add("busy");
    try {
      await api("/api/settings", { method: "PUT", body: { keys } });
      toast("Settings saved", "ok");
      load(); pollHealth();
    } catch (e) { toast(e.message, "error"); }
    finally { btn.disabled = false; btn.classList.remove("busy"); }
  });

  onEnter.settings = load;
})();

/* ── shared with studio.js (Director, Templates, Projects, Brands…) ── */

window.champ = { $, $$, el, api, toast, pref, setPref, fmtDate, shortModel, download, openModal, pickFromLibrary,
                 chipGroup, bindSwitch, autoGrow, show, onEnter, mediaFor, openDetail, pollHealth };

/* ── boot (after every module has registered its views) ─────────── */

document.addEventListener("DOMContentLoaded", () => {
  const initial = location.hash.replace(/^#\//, "");
  show($(`#view-${initial}`) ? initial : "director");
});
