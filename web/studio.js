/* Champ Studio — Director, Templates, Editor projects, Brand Kits, usage/models/team settings.
   Buildless ES module; shares helpers with app.js through window.champ. */

const { $, el, api, toast, pref, setPref, fmtDate, openModal, pickFromLibrary, chipGroup, bindSwitch, autoGrow, show,
        onEnter } = window.champ;

const ASPECTS = ["16:9", "9:16", "1:1", "4:5"];
const MOODS = ["corporate-upbeat", "calm-tech", "inspiring", "energetic", "chill", "dramatic", "none"];

/* ── shared helpers ─────────────────────────────────────────────── */

function multiChips(container, options, storeKey, def) {
  let value = new Set(pref(storeKey, def));
  const chips = options.map((o) => el("button", {
    class: `chip${value.has(o.value) ? " active" : ""}`, type: "button",
    onclick: () => {
      if (value.has(o.value) && value.size > 1) value.delete(o.value); else value.add(o.value);
      setPref(storeKey, [...value]);
      chips.forEach((c, i) => c.classList.toggle("active", value.has(options[i].value)));
    },
  }, o.label));
  container.append(...chips);
  return { get value() { return options.map((o) => o.value).filter((v) => value.has(v)); } };
}

/** Poll a job until it finishes; `onTick(job)` sees progress. Resolves with the job. */
async function waitJob(jobId, onTick) {
  for (;;) {
    const job = await api(`/api/jobs/${jobId}`);
    onTick?.(job);
    if (["completed", "failed", "canceled"].includes(job.status)) return job;
    await new Promise((r) => setTimeout(r, job.status === "waiting" ? 1500 : 900));
  }
}

function progressBar() {
  const fill = el("div", { class: "pbar-fill" });
  const label = el("span", { class: "pbar-label" }, "Starting…");
  const t0 = Date.now();
  const wrap = el("div", { class: "pbar" }, el("div", { class: "pbar-track" }, fill), label);
  return {
    el: wrap,
    set(job) {
      const pct = Math.round((job.progress || 0) * 100);
      fill.style.width = `${Math.max(3, pct)}%`;
      const secs = Math.round((Date.now() - t0) / 1000);
      label.textContent = job.status === "failed" ? `Failed — ${job.error}` :
        job.status === "completed" ? "Done" : `${job.stage || job.status} · ${pct}% · ${secs}s`;
      wrap.classList.toggle("err", job.status === "failed");
      wrap.classList.toggle("ok", job.status === "completed");
    },
  };
}

async function runJob(path, body, host, { done } = {}) {
  const bar = progressBar();
  host.replaceChildren(bar.el);
  try {
    const { job_id } = await api(path, { method: "POST", body });
    const job = await waitJob(job_id, (j) => bar.set(j));
    if (job.status === "completed") done?.(job.result || {});
    else toast(job.error || "Job failed", "error");
    return job;
  } catch (e) {
    bar.set({ status: "failed", error: e.message });
    toast(e.message, "error");
    return null;
  }
}

let brandCache = null;
async function brandsList(force = false) {
  if (!brandCache || force) brandCache = (await api("/api/brands")).brands;
  return brandCache;
}
async function fillBrandSelect(select, storeKey) {
  const kits = await brandsList();
  const current = pref(storeKey, "champions-group");
  select.replaceChildren(...kits.map((k) => el("option", { value: k.id, selected: k.id === current }, k.name)));
  select.onchange = () => setPref(storeKey, select.value);
}

const money = (v) => `$${Number(v || 0).toFixed(v >= 1 ? 2 : 3)}`;
const openEditor = (pid) => window.open(`/editor/?project=${pid}`, "_blank");

function videoPlayer(assetId) {
  return el("video", { class: "media", src: `/api/assets/${assetId}/file`, controls: true, playsinline: true,
                       preload: "metadata" });
}

async function shareForReview(projectId, renderAssetId) {
  try {
    const { url } = await api(`/api/projects/${projectId}/review-links`, { method: "POST",
                                                                           body: { render_asset_id: renderAssetId } });
    const full = `${location.origin}${url}`;
    await navigator.clipboard?.writeText(full).catch(() => {});
    openModal("Review link", el("div", {},
      el("p", {}, "Anyone with this link can watch, comment at exact frames, and approve or request changes:"),
      el("input", { class: "sel wide", value: full, readonly: true, onclick: (e) => e.target.select() }),
      el("p", { class: "hint" }, "Copied to the clipboard. Comments show up in the editor as markers.")));
  } catch (e) { toast(e.message, "error"); }
}

/* ── DIRECTOR ───────────────────────────────────────────────────── */

const director = (() => {
  const length = chipGroup($("#dir-length"), [15, 30, 45, 60, 90].map((v) => ({ value: v, label: `${v}s` })),
                           "dir.length", 30);
  const aspects = multiChips($("#dir-aspects"), ASPECTS.map((a) => ({ value: a, label: a })), "dir.aspects",
                             ["16:9", "9:16"]);
  const BUDGETS = {
    lean: "Stills with motion and text cards — cents per video.",
    balanced: "AI video for the hook and up to two key scenes, stills elsewhere — about $1–2 for 30 s.",
    premium: "AI video for nearly every scene — about $3–4 for 30 s at 1080p.",
  };
  const budget = chipGroup($("#dir-budget"), Object.keys(BUDGETS).map((v) => ({ value: v, label: v[0].toUpperCase() + v.slice(1) })),
                           "dir.budget", "balanced");
  const hint = () => { $("#dir-budget-hint").textContent = BUDGETS[budget.value]; };
  $("#dir-budget").addEventListener("click", hint);
  hint();
  const auto = bindSwitch($("#dir-auto"), "dir.auto", false);
  autoGrow($("#dir-prompt"));
  let presenter = null;
  $("#dir-presenter").addEventListener("click", async () => {
    const a = await pickFromLibrary({ kinds: ["image"] });
    presenter = a;
    $("#dir-presenter").textContent = a ? "✓ Presenter set" : "+ Pick image";
  });

  async function create() {
    const prompt = $("#dir-prompt").value.trim();
    if (prompt.length < 3) { toast("Describe the video first"); return; }
    const btn = $("#dir-go");
    btn.disabled = true; btn.classList.add("busy");
    try {
      const body = {
        brief: { prompt, length_s: length.value, budget: budget.value, platform: $("#dir-platform").value,
                 audience: $("#dir-audience").value || null, facts: $("#dir-facts").value || null,
                 presenter_asset_id: presenter?.id || null },
        brand_kit_id: $("#dir-brand").value, aspects: aspects.value, autopilot: auto.value,
      };
      const r = await api("/api/director", { method: "POST", body });
      $("#dir-prompt").value = "";
      open(r.project_id, r.job_id);
    } catch (e) { toast(e.message, "error"); }
    finally { btn.disabled = false; btn.classList.remove("busy"); }
  }
  $("#dir-go").addEventListener("click", create);
  $("#dir-prompt").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) create(); });

  async function list() {
    fillBrandSelect($("#dir-brand"), "dir.brand").catch(() => {});
    try {
      const { projects } = await api("/api/director");
      $("#dir-list").replaceChildren(...projects.map((p) => el("button", { class: "project-card", onclick: () => open(p.id) },
        el("div", { class: "pc-title" }, p.name),
        el("div", { class: "pc-meta" }, `Updated ${fmtDate(new Date(p.updated_at * 1000).toISOString())}`))));
      $("#dir-empty").hidden = projects.length > 0;
    } catch (e) { toast(e.message, "error"); }
  }

  /* detail */
  let current = null;
  async function open(pid, jobId) {
    $("#dir-new").hidden = true;
    const host = $("#dir-detail");
    host.hidden = false;
    host.replaceChildren(el("div", { class: "combo-empty" }, "Loading…"));
    current = pid;
    if (jobId) await follow(jobId);
    render();
  }

  async function follow(jobId) {
    const host = $("#dir-detail");
    const bar = progressBar();
    host.replaceChildren(el("button", { class: "btn ghost sm", onclick: close }, "← All projects"),
                         el("div", { class: "glass job-host" }, bar.el));
    const job = await waitJob(jobId, (j) => bar.set(j));
    if (job.status !== "completed") toast(job.error || "Step failed", "error");
  }

  function close() { current = null; $("#dir-detail").hidden = true; $("#dir-new").hidden = false; list(); }

  async function step(path, body = {}) {
    try {
      const r = await api(`/api/director/${current}/${path}`, { method: "POST", body });
      if (r.job_id) await follow(r.job_id);
      if (r.job?.job_id) await follow(r.job.job_id);
      render();
      return r;
    } catch (e) { toast(e.message, "error"); return null; }
  }

  async function render() {
    if (!current) return;
    const host = $("#dir-detail");
    let p;
    try { p = await api(`/api/director/${current}`); } catch (e) { toast(e.message, "error"); return; }
    const d = p.data;
    const plan = d.plan;
    const state = d.scene_state || {};
    if (!plan) {
      host.replaceChildren(el("button", { class: "btn ghost sm", onclick: close }, "← All projects"),
        el("div", { class: "glass job-host" }, el("p", {}, "No plan yet."),
          el("button", { class: "btn ember", onclick: () => step("plan") }, "Write plan")));
      return;
    }
    const stage = d.stage || "planned";
    const est = p.estimate?.usd;
    const actions = el("div", { class: "detail-actions" },
      el("button", { class: "btn", onclick: () => step("storyboard") }, "1 · Storyboard"),
      el("button", { class: "btn", onclick: () => step("produce", { quality: "draft" }) }, "2 · Produce draft"),
      el("button", { class: "btn", onclick: () => step("produce", { quality: "final" }) }, "Produce final (1080p)"),
      el("button", { class: "btn ember", disabled: !d.timelines, onclick: () => step("render", { preset: "h264" }) }, "3 · Render"),
      el("button", { class: "btn ghost", onclick: () => step("autopilot") }, "Autopilot"));
    const head = el("div", { class: "dir-head" },
      el("button", { class: "btn ghost sm", onclick: close }, "← All projects"),
      el("div", {},
        el("h2", {}, plan.title),
        el("p", { class: "hint" }, plan.logline),
        el("div", { class: "dir-pills" },
          el("span", { class: `status-pill ${stage}` }, stage),
          el("span", { class: "pill-soft" }, `${plan.scenes.length} scenes`),
          el("span", { class: "pill-soft" }, `${Math.round(plan.scenes.reduce((a, s) => a + s.duration_s, 0))} s`),
          el("span", { class: "pill-soft" }, `${(d.aspects || []).join(" · ")}`),
          est != null && el("span", { class: "pill-soft", title: "Estimated cost to produce at the chosen quality" },
                            `≈ ${money(est)} to produce`),
          el("span", { class: "pill-soft" }, `Music: ${plan.music_mood}`))),
      actions);

    const rows = plan.scenes.map((s, i) => sceneRow(s, i, state[s.id] || {}));
    const saveBtn = el("button", { class: "btn", onclick: () => savePlan(plan, rows) }, "Save script changes");

    const renders = d.renders ? el("div", { class: "render-grid" }, ...Object.entries(d.renders).map(([a, id]) =>
      el("div", { class: "render-card" }, videoPlayer(id),
        el("div", { class: "card-actions static" },
          el("span", { class: "badge video" }, a),
          el("a", { class: "btn sm", href: `/api/assets/${id}/file`, download: "" }, "Download"),
          el("button", { class: "btn sm", onclick: () => toEditor(a, id) }, "Share for review"))))) : null;

    const chatLog = el("div", { class: "chat-log" }, ...(d.history || []).map((m) =>
      el("div", { class: `chat-msg ${m.role}` }, m.content)));
    const chatInput = el("input", { class: "sel wide", placeholder: "e.g. make the hook punchier, swap scene 3 for a hospital visual, add a scene about HIPAA…" });
    const chatGo = el("button", { class: "btn ember", onclick: async () => {
      const msg = chatInput.value.trim();
      if (!msg) return;
      chatGo.disabled = true;
      chatLog.append(el("div", { class: "chat-msg user" }, msg));
      chatInput.value = "";
      const r = await step("chat", { message: msg, apply: true });
      if (r) toast(r.summary, "ok");
      chatGo.disabled = false;
    } }, "Apply");
    chatInput.addEventListener("keydown", (e) => { if (e.key === "Enter") chatGo.click(); });

    const editorBtns = d.timelines ? el("div", { class: "detail-actions" },
      ...Object.keys(d.timelines).map((a) => el("button", { class: "btn ghost", onclick: async () => {
        try {
          const proj = await api(`/api/director/${current}/open-in-editor`, { method: "POST", body: { aspect: a } });
          openEditor(proj.id);
        } catch (e) { toast(e.message, "error"); }
      } }, `Open ${a} in editor`))) : null;

    host.replaceChildren(head,
      (d.warnings || []).length ? el("div", { class: "warn-box" }, "Some clips fell back to stills: ", d.warnings.join(" · ")) : "",
      renders ? el("h2", { class: "section-title" }, "Renders") : "", renders || "",
      el("h2", { class: "section-title" }, "Edit by chat"),
      el("div", { class: "glass chat-box" }, chatLog, el("div", { class: "chat-row" }, chatInput, chatGo)),
      el("h2", { class: "section-title" }, "Script & storyboard"),
      el("div", { class: "scene-list" }, ...rows.map((r) => r.el)),
      el("div", { class: "detail-actions" }, saveBtn),
      editorBtns ? el("h2", { class: "section-title" }, "Fine-tune in the editor") : "", editorBtns || "");

    async function toEditor(aspect, renderId) {
      try {
        const proj = await api(`/api/director/${current}/open-in-editor`, { method: "POST", body: { aspect } });
        shareForReview(proj.id, renderId);
      } catch (e) { toast(e.message, "error"); }
    }
  }

  function sceneRow(s, i, st) {
    const field = (tag, key, attrs = {}) => {
      const node = el(tag, { class: "sel wide", ...attrs });
      node.value = s[key] ?? "";
      return node;
    };
    const vo = field("textarea", "voiceover", { rows: 2 });
    const ost = field("input", "on_screen_text");
    const visual = field("textarea", "visual", { rows: 2 });
    const vtype = el("select", { class: "sel" }, ...["ai_video", "ai_image", "text_card", "avatar", "upload"]
      .map((v) => el("option", { value: v, selected: v === s.visual_type }, v.replace("_", " "))));
    const dur = el("input", { class: "sel num", type: "number", min: 1.5, max: 15, step: 0.5, value: s.duration_s });
    const trans = el("select", { class: "sel" }, ...["cut", "crossfade", "dip-black", "wipe-left", "slide-left", "slide-up", "zoom"]
      .map((v) => el("option", { value: v, selected: v === s.transition }, v)));
    const thumb = st.clip_id ? el("video", { src: `/api/assets/${st.clip_id}/file`, muted: true, loop: true, playsinline: true,
                                            onmouseenter: (e) => e.target.play(), onmouseleave: (e) => e.target.pause() })
      : st.still_id ? el("img", { src: `/api/assets/${st.still_id}/file`, alt: "" })
      : el("div", { class: "scene-ph" }, s.visual_type === "text_card" ? "Text card" : "No still yet");
    const regen = el("button", { class: "btn sm ghost", onclick: async () => {
      await (async () => {
        try {
          const r = await api(`/api/director/${current}/storyboard`, { method: "POST", body: { scene_ids: [s.id], force: true } });
          await waitJob(r.job_id);
          render();
        } catch (e) { toast(e.message, "error"); }
      })();
    } }, "↻ New still");
    const node = el("div", { class: "scene-row glass" },
      el("div", { class: "scene-media" }, thumb, el("div", { class: "scene-num" }, `${i + 1} · ${s.purpose}`),
         s.visual_type !== "text_card" && regen),
      el("div", { class: "scene-fields" },
        el("label", {}, "Voiceover"), vo,
        el("label", {}, "On-screen text"), ost,
        el("label", {}, "Visual"), visual,
        el("div", { class: "scene-inline" },
          el("div", {}, el("label", {}, "Type"), vtype),
          el("div", {}, el("label", {}, "Seconds"), dur),
          el("div", {}, el("label", {}, "Transition in"), trans)),
        st.error && el("div", { class: "job-error" }, st.error)));
    return {
      el: node,
      read: () => ({ ...s, voiceover: vo.value, on_screen_text: ost.value, visual: visual.value,
                     visual_type: vtype.value, duration_s: Number(dur.value), transition: trans.value }),
    };
  }

  async function savePlan(plan, rows) {
    const next = { ...plan, scenes: rows.map((r) => r.read()) };
    try {
      await api(`/api/director/${current}/plan`, { method: "PUT", body: { plan: next } });
      toast("Script saved — changed scenes will be re-made on the next Produce", "ok");
      render();
    } catch (e) { toast(e.message, "error"); }
  }

  onEnter.director = () => { if (!current) list(); };
  return { open };
})();

/* ── TEMPLATES ──────────────────────────────────────────────────── */

const templatesView = (() => {
  let all = [];
  let cats = {};
  let cat = pref("tpl.cat", "");
  const aspect = chipGroup($("#tpl-aspect"), ASPECTS.map((a) => ({ value: a, label: a })), "tpl.aspect", "16:9");
  $("#tpl-aspect").addEventListener("click", () => setTimeout(paint));
  $("#tpl-search").addEventListener("input", () => paint());
  $("#tpl-brand").addEventListener("change", () => paint());

  async function load() {
    await fillBrandSelect($("#tpl-brand"), "tpl.brand").catch(() => {});
    try {
      const r = await api("/api/templates");
      all = r.templates;
      cats = r.categories;
      const chips = [["", "All"], ...Object.entries(cats)].map(([k, label]) =>
        el("button", { class: `chip${k === cat ? " active" : ""}`, onclick: () => { cat = k; setPref("tpl.cat", k); load(); } },
           label));
      $("#tpl-cats").replaceChildren(...chips);
      paint();
    } catch (e) { toast(e.message, "error"); }
  }

  function paint() {
    const q = $("#tpl-search").value.trim().toLowerCase();
    const brand = $("#tpl-brand").value;
    const items = all.filter((t) => (!cat || t.category === cat) && (!q || JSON.stringify(t).toLowerCase().includes(q))
      && (!t.aspects?.length || t.aspects.includes(aspect.value)));
    $("#tpl-grid").replaceChildren(...items.map((t) => el("button", { class: `tpl-card ratio-${aspect.value.replace(":", "x")}`,
                                                                      onclick: () => detail(t.id) },
      el("div", { class: "tpl-thumb" },
        el("img", { loading: "lazy", alt: "", src: `/api/templates/${t.id}/preview.jpg?aspect=${encodeURIComponent(aspect.value)}&brand=${brand}` })),
      el("div", { class: "tpl-info" },
        el("div", { class: "pc-title" }, t.name),
        el("div", { class: "pc-meta" }, `${cats[t.category] || t.category} · ${t.scenes} scene${t.scenes > 1 ? "s" : ""} · ~${t.duration_hint}s`),
        t.origin === "team" && el("span", { class: "pill-soft" }, "Team")))));
    if (!items.length) $("#tpl-grid").append(el("div", { class: "combo-empty" }, "No templates match."));
  }

  async function detail(tid) {
    let t;
    try { t = await api(`/api/templates/${tid}`); } catch (e) { toast(e.message, "error"); return; }
    const values = { ...(t.defaults || {}) };
    const form = el("div", { class: "slot-form" });
    const inputs = {};
    for (const [name, prop] of Object.entries(t.slots.properties)) {
      const kind = prop["x-kind"] || "text";
      const label = el("label", {}, prop.description || name, prop.maxLength ? el("small", {}, ` ≤${prop.maxLength}`) : "");
      let input;
      if (kind === "list") {
        input = el("textarea", { class: "sel wide", rows: 4, placeholder: "One item per line" });
        input.value = (values[name] || []).join("\n");
        inputs[name] = () => input.value.split("\n").map((x) => x.trim()).filter(Boolean);
      } else if (kind === "color") {
        input = el("input", { type: "color", class: "color-in", value: values[name] || "#F26722" });
        inputs[name] = () => input.value.toUpperCase();
      } else if (kind === "music") {
        input = el("select", { class: "sel" }, ...MOODS.map((m) => el("option", { value: m, selected: m === values[name] }, m)));
        inputs[name] = () => input.value;
      } else if (kind === "media" || kind === "logo") {
        const status = el("span", { class: "hint" }, values[name]?.asset_id ? "Using a library asset" : "Leave empty to generate from the prompt");
        const prompt = el("input", { class: "sel wide", placeholder: "Describe the visual to generate (no text/logos)…" });
        if (values[name]?.prompt) prompt.value = values[name].prompt;
        let picked = values[name]?.asset_id || null;
        input = el("div", { class: "media-slot" }, prompt,
          el("button", { class: "btn sm ghost", type: "button", onclick: async () => {
            const a = await pickFromLibrary({ kinds: ["image", "video"] });
            if (a) { picked = a.id; status.textContent = "✓ Using a library asset"; }
          } }, "From library"), status);
        inputs[name] = () => picked ? { asset_id: picked } : prompt.value.trim() ? { prompt: prompt.value.trim(), media_type: "image" } : undefined;
      } else if (kind === "voiceover" || (prop.maxLength || 0) > 90) {
        input = el("textarea", { class: "sel wide", rows: 2, maxlength: prop.maxLength || 600 });
        input.value = values[name] || "";
        inputs[name] = () => input.value.trim() || undefined;
      } else if (kind === "number") {
        input = el("input", { class: "sel", type: "number", value: values[name] ?? "" });
        inputs[name] = () => input.value === "" ? undefined : Number(input.value);
      } else {
        input = el("input", { class: "sel wide", maxlength: prop.maxLength || 200, value: values[name] || "" });
        inputs[name] = () => input.value.trim() || undefined;
      }
      form.append(el("div", { class: `slot slot-${kind}` }, label, input));
    }
    const collect = () => Object.fromEntries(Object.entries(inputs).map(([k, f]) => [k, f()]).filter(([, v]) => v !== undefined));
    const setValues = (vals) => {
      for (const [k, v] of Object.entries(vals)) {
        const slot = form.querySelector(`.slot:nth-child(${Object.keys(inputs).indexOf(k) + 1})`);
        if (!slot) continue;
        const input = slot.querySelector("textarea, input:not([type=color]), select, input[type=color]");
        if (Array.isArray(v)) input.value = v.join("\n");
        else if (v && typeof v === "object") { if (v.prompt) slot.querySelector("input").value = v.prompt; }
        else if (input) input.value = v;
      }
    };

    const brief = el("textarea", { class: "sel wide", rows: 3, placeholder: "Brief for AI fill — product, audience, offer, facts, tone…" });
    const fillBtn = el("button", { class: "btn", onclick: async () => {
      if (brief.value.trim().length < 3) { toast("Write a brief first"); return; }
      fillBtn.disabled = true; fillBtn.classList.add("busy");
      try {
        const r = await api(`/api/templates/${tid}/fill`, { method: "POST", body: { brief: brief.value, brand_kit_id: $("#tpl-brand").value } });
        setValues(r.values);
        toast("Slots filled — review and adjust", "ok");
      } catch (e) { toast(e.message, "error"); }
      finally { fillBtn.disabled = false; fillBtn.classList.remove("busy"); }
    } }, "✦ Fill with AI");

    const aspects = el("div", { class: "chip-row multi" });
    const aspectPick = multiChips(aspects, (t.aspects || ASPECTS).map((a) => ({ value: a, label: a })), `tpl.out.${tid}`, [aspect.value]);
    const opt = (label, key, def) => {
      const sw = el("button", { class: "switch", role: "switch", "aria-checked": "false", type: "button" }, el("span"));
      const b = bindSwitch(sw, key, def);
      return { el: el("div", { class: "ctl" }, el("label", {}, label), sw), get value() { return b.value; } };
    };
    const vo = opt("Voiceover", "tpl.vo", !t.overlay);
    const music = opt("Music", "tpl.music", !t.overlay);
    const vid = opt("AI video for media", "tpl.video", false);
    const out = el("div", { class: "job-host" });
    const go = el("button", { class: "btn ember big", onclick: () => runJob(`/api/templates/${tid}/compose`, {
      values: collect(), brand_kit_id: $("#tpl-brand").value, aspects: aspectPick.value, voiceover: vo.value,
      music: music.value, allow_video: vid.value, render: true, preset: "h264",
    }, out, { done: (res) => {
      out.replaceChildren(el("div", { class: "render-grid" }, ...Object.entries(res.renders || {}).map(([a, id]) =>
        el("div", { class: "render-card" }, videoPlayer(id), el("div", { class: "card-actions static" },
          el("span", { class: "badge video" }, a),
          el("a", { class: "btn sm", href: `/api/assets/${id}/file`, download: "" }, "Download"),
          el("button", { class: "btn sm", onclick: () => openEditor(res.projects[a]) }, "Open in editor"),
          el("button", { class: "btn sm", onclick: () => shareForReview(res.projects[a], id) }, "Share for review"))))));
    } }) }, "Create video");

    const csv = el("textarea", { class: "sel wide", rows: 5, placeholder: "first_name,company,cta\nDana,Acme,Book a call\n…  (columns named like slots replace them; any column can be merged into text as {column})" });
    const batchOut = el("div", { class: "job-host" });
    const batchGo = el("button", { class: "btn", onclick: () => runJob(`/api/templates/${tid}/batch`, {
      csv: csv.value, brand_kit_id: $("#tpl-brand").value, aspect: aspectPick.value[0], voiceover: vo.value, music: music.value,
    }, batchOut, { done: (res) => {
      batchOut.replaceChildren(el("p", { class: "hint" }, `${res.ok} of ${res.results.length} videos rendered`),
        el("div", { class: "batch-list" }, ...res.results.map((r) => r.asset_id
          ? el("a", { class: "btn sm", href: `/api/assets/${r.asset_id}/file`, download: "" }, `⬇ ${r.label}`)
          : el("span", { class: "job-error" }, `Row ${r.row + 1}: ${r.error}`))));
    } }) }, "Render batch");

    const body = el("div", { class: "tpl-detail" },
      el("div", { class: "tpl-side" },
        el("img", { class: "tpl-big", src: `/api/templates/${tid}/preview.jpg?aspect=${encodeURIComponent(aspect.value)}&brand=${$("#tpl-brand").value}&height=480`, alt: "" }),
        el("p", {}, t.description || ""),
        el("div", { class: "dir-pills" }, ...(t.tags || []).map((x) => el("span", { class: "pill-soft" }, x))),
        el("h3", { class: "sub-title" }, "Fill from a brief"), brief, fillBtn),
      el("div", { class: "tpl-main" },
        form,
        el("div", { class: "controls-row" }, el("div", { class: "ctl" }, el("label", {}, "Ratios"), aspects), vo.el, music.el, vid.el),
        go, out,
        el("details", { class: "batch" }, el("summary", {}, "Batch: personalised videos from a CSV"), csv, batchGo, batchOut)));
    openModal(t.name, body, { wide: true });
  }

  onEnter.templates = load;
  return { load };
})();

/* ── EDITOR PROJECTS ────────────────────────────────────────────── */

(() => {
  const aspect = chipGroup($("#proj-aspect"), ASPECTS.map((a) => ({ value: a, label: a })), "proj.aspect", "16:9");
  async function list() {
    try {
      const { projects } = await api("/api/projects?kind=edit");
      $("#proj-list").replaceChildren(...projects.map((p) => el("div", { class: "project-card" },
        el("div", { class: "pc-title" }, p.name),
        el("div", { class: "pc-meta" }, `v${p.version} · ${fmtDate(new Date(p.updated_at * 1000).toISOString())}`,
           p.locked_by ? ` · editing: ${p.locked_by}` : ""),
        el("div", { class: "card-actions static" },
          el("button", { class: "btn sm ember", onclick: () => openEditor(p.id) }, "Open"),
          el("a", { class: "btn sm", href: `/api/projects/${p.id}/interchange?format=fcp_xml` }, "Premiere XML"),
          el("button", { class: "btn sm danger", onclick: async () => {
            if (!confirm(`Delete "${p.name}"? This can't be undone.`)) return;
            try { await api(`/api/projects/${p.id}`, { method: "DELETE" }); list(); } catch (e) { toast(e.message, "error"); }
          } }, "Delete")))));
      $("#proj-empty").hidden = projects.length > 0;
    } catch (e) { toast(e.message, "error"); }
  }
  $("#proj-new").addEventListener("click", async () => {
    try {
      const p = await api("/api/projects", { method: "POST", body: { name: $("#proj-name").value || "Untitled sequence", aspect: aspect.value } });
      openEditor(p.id);
      list();
    } catch (e) { toast(e.message, "error"); }
  });
  $("#proj-import").addEventListener("click", () => $("#proj-import-file").click());
  $("#proj-import-file").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    const form = new FormData();
    form.append("file", f);
    try { const p = await api("/api/projects/import", { method: "POST", form }); toast(`Imported "${p.name}"`, "ok"); list(); }
    catch (err) { toast(err.message, "error"); }
  });
  $("#proj-rough").addEventListener("click", () => {
    const chosen = [];
    const listEl = el("div", { class: "dir-pills" });
    const brief = el("textarea", { class: "sel wide", rows: 3, placeholder: "What story should the cut tell? Audience, key message, tone…" });
    const target = el("input", { class: "sel num", type: "number", value: 60, min: 10, max: 600 });
    const out = el("div", { class: "job-host" });
    const body = el("div", {},
      el("p", { class: "hint" }, "Pick interviews or recordings. Champ Studio transcribes them and the AI assembles the strongest story — a starting cut you then refine."),
      el("button", { class: "btn ghost", onclick: async () => {
        const a = await pickFromLibrary({ kinds: ["video", "audio"] });
        if (a && !chosen.includes(a.id)) { chosen.push(a.id); listEl.append(el("span", { class: "pill-soft" }, a.prompt || a.id)); }
      } }, "+ Add footage"), listEl,
      el("label", {}, "Brief"), brief, el("label", {}, "Target length (s)"), target,
      el("div", { class: "detail-actions" }, el("button", { class: "btn ember", onclick: () => {
        if (!chosen.length) { toast("Add footage first"); return; }
        runJob("/api/ai/rough-cut", { asset_ids: chosen, brief: brief.value || "best story", target_s: Number(target.value),
                                      aspect: aspect.value }, out, { done: (r) => { openEditor(r.project_id); list(); } });
      } }, "Make rough cut")), out);
    openModal("AI rough cut", body);
  });
  onEnter.projects = list;
})();

/* ── BRAND KITS ─────────────────────────────────────────────────── */

(() => {
  async function list() {
    try {
      const kits = await brandsList(true);
      $("#brand-grid").replaceChildren(...kits.map((k) => el("button", { class: "brand-card glass", onclick: () => edit(k) },
        el("div", { class: "swatches" }, ...["primary", "primary_dark", "accent", "background", "text"].map((c) =>
          el("span", { style: `background:${k.colors?.[c] || "#000"}`, title: `${c} ${k.colors?.[c]}` }))),
        el("div", { class: "pc-title", style: `font-family:${k.fonts?.heading?.family || "Inter"}` }, k.name),
        el("div", { class: "pc-meta" }, k.tagline || k.tone?.slice(0, 80) || ""))));
    } catch (e) { toast(e.message, "error"); }
  }

  async function edit(kit) {
    const k = structuredClone(kit || { name: "New brand", colors: {}, fonts: { heading: {}, body: {} }, logos: {} });
    const { families } = await api("/api/fonts").catch(() => ({ families: ["Inter"] }));
    const colorKeys = ["primary", "primary_dark", "accent", "background", "surface", "text", "text_muted", "on_primary", "dark"];
    const colorInputs = colorKeys.map((c) => {
      const inp = el("input", { type: "color", class: "color-in", value: (k.colors?.[c] || "#000000").slice(0, 7) });
      return { c, inp, node: el("label", { class: "color-field" }, inp, c.replace("_", " ")) };
    });
    const fontSel = (role) => el("select", { class: "sel" }, ...families.map((f) =>
      el("option", { value: f, selected: f === (k.fonts?.[role]?.family || "Inter") }, f)));
    const head = fontSel("heading"), bodyF = fontSel("body");
    const text = (key, ph) => { const i = el("input", { class: "sel wide", placeholder: ph }); i.value = k[key] || ""; return i; };
    const name = text("name", "Brand name"), tagline = text("tagline", "Tagline"), website = text("website", "website.com"),
          cta = text("cta", "Default call to action");
    const tone = el("textarea", { class: "sel wide", rows: 3, placeholder: "Brand voice and tone for scripts" });
    tone.value = k.tone || "";
    const voice = el("input", { class: "sel", placeholder: "Kore", value: k.voice?.voice || "" });
    const mood = el("select", { class: "sel" }, ...MOODS.map((m) => el("option", { value: m, selected: m === k.music_mood }, m)));
    const logoImg = el("img", { class: "logo-prev", alt: "", src: k.logos?.primary ? `/api/assets/${k.logos.primary}/file` : "" });
    const pickLogo = el("button", { class: "btn sm ghost", type: "button", onclick: async () => {
      const a = await pickFromLibrary({ kinds: ["image", "vector"] });
      if (a) { k.logos = { ...(k.logos || {}), primary: a.id }; logoImg.src = a.url; }
    } }, "Pick logo (PNG with transparency)");
    const save = el("button", { class: "btn ember", onclick: async () => {
      for (const { c, inp } of colorInputs) k.colors[c] = inp.value.toUpperCase();
      Object.assign(k, { name: name.value.trim() || "Untitled brand", tagline: tagline.value, website: website.value,
                         cta: cta.value, tone: tone.value, music_mood: mood.value });
      k.fonts = { heading: { family: head.value, weight: 800 }, body: { family: bodyF.value, weight: 500 } };
      k.voice = { ...(k.voice || {}), voice: voice.value || null };
      k.gradients = { hero: [k.colors.primary, k.colors.primary_dark], sunset: [k.colors.accent, k.colors.primary] };
      const st = k.styles || {};
      const hf = head.value, bf = bodyF.value;
      k.styles = {
        ...st,
        title: { ...(st.title || {}), font: hf, weight: 800, color: st.title?.color || "#FFFFFF" },
        subtitle: { ...(st.subtitle || {}), font: bf, weight: 600 },
        lower_third: { ...(st.lower_third || {}), font: bf, bg_color: k.colors.primary },
        button: { ...(st.button || {}), font: bf, bg_color: k.colors.primary, color: k.colors.on_primary },
        captions: { ...(st.captions || {}), font: hf, highlight_color: k.colors.accent },
        stat: { ...(st.stat || {}), font: hf, color: k.colors.primary },
      };
      try { await api("/api/brands", { method: "POST", body: k }); toast("Brand kit saved", "ok"); close(); list(); }
      catch (e) { toast(e.message, "error"); }
    } }, "Save brand kit");
    const close = openModal(kit ? `Edit ${kit.name}` : "New brand kit", el("div", { class: "brand-form" },
      el("label", {}, "Name"), name,
      el("label", {}, "Colours"), el("div", { class: "color-grid" }, ...colorInputs.map((x) => x.node)),
      el("div", { class: "scene-inline" }, el("div", {}, el("label", {}, "Heading font"), head),
                                           el("div", {}, el("label", {}, "Body font"), bodyF),
                                           el("div", {}, el("label", {}, "Voice"), voice),
                                           el("div", {}, el("label", {}, "Music mood"), mood)),
      el("label", {}, "Logo"), el("div", { class: "logo-row" }, logoImg, pickLogo),
      el("label", {}, "Tagline"), tagline, el("label", {}, "Website"), website, el("label", {}, "Default CTA"), cta,
      el("label", {}, "Tone of voice"), tone,
      el("div", { class: "detail-actions" }, save)), { wide: true });
  }

  $("#brand-new").addEventListener("click", () => edit(null));
  $("#brand-font").addEventListener("click", () => $("#brand-font-file").click());
  $("#brand-font-file").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    const form = new FormData();
    form.append("file", f);
    try { await api("/api/fonts", { method: "POST", form }); toast(`Font ${f.name} added`, "ok"); }
    catch (err) { toast(err.message, "error"); }
  });
  onEnter.brands = list;
})();

/* ── SETTINGS: usage, budget, models, team ──────────────────────── */

(() => {
  const prevSettings = onEnter.settings;
  async function load() {
    prevSettings?.();
    try {
      const c = await api("/api/costs?days=30");
      $("#budget-month").value = c.budget.monthly_usd || "";
      $("#budget-project").value = c.budget.per_project_usd || "";
      $("#usage-summary").replaceChildren(
        el("div", { class: "stat" }, el("b", {}, money(c.month_to_date)), el("span", {}, "this month")),
        el("div", { class: "stat" }, el("b", {}, money(c.total)), el("span", {}, "last 30 days")),
        ...c.by_kind.map((k) => el("div", { class: "stat" }, el("b", {}, money(k.cost)), el("span", {}, `${k.kind} · ${k.n} calls`))));
      $("#usage-models").replaceChildren(el("tr", {}, el("th", {}, "Model"), el("th", {}, "Calls"), el("th", {}, "Cost (30 d)")),
        ...c.by_model.map((m) => el("tr", {}, el("td", {}, el("code", {}, m.model)), el("td", {}, m.n), el("td", {}, money(m.cost)))));
    } catch (e) { toast(e.message, "error"); }
    try {
      const r = await api("/api/roles");
      $("#roles-table").replaceChildren(el("tr", {}, el("th", {}, "Job"), el("th", {}, "Model"), el("th", {}, "Fallback")),
        ...Object.entries(r.roles).map(([name, role]) => el("tr", { dataset: { role: name } },
          el("td", {}, el("b", {}, name), el("div", { class: "hint" }, role.label || "")),
          el("td", {}, el("input", { class: "sel wide mono", value: role.model || "", placeholder: r.defaults[name]?.model || "" })),
          el("td", {}, el("input", { class: "sel wide mono", value: role.fallback || "", placeholder: r.defaults[name]?.fallback || "" })))));
    } catch (e) { toast(e.message, "error"); }
    try {
      const me = await api("/api/me");
      const card = $("#team-card");
      card.hidden = me.role !== "admin";
      if (me.role === "admin") {
        const [{ users }, { entries }] = await Promise.all([api("/api/users"), api("/api/audit?limit=40")]);
        $("#users-table").replaceChildren(el("tr", {}, el("th", {}, "User"), el("th", {}, "Role")),
          ...(users.length ? users : [{ id: "local", email: "Local (single-user mode — enable SSO for teams)", role: "admin" }]).map((u) =>
            el("tr", {}, el("td", {}, u.email), el("td", {}, u.id === "local" ? u.role : el("select", {
              class: "sel", onchange: async (e) => {
                try { await api(`/api/users/${u.id}/role`, { method: "PUT", body: { role: e.target.value } }); toast("Role updated", "ok"); }
                catch (err) { toast(err.message, "error"); }
              } }, ...["viewer", "reviewer", "editor", "admin"].map((r) => el("option", { value: r, selected: r === u.role }, r)))))));
        $("#audit-table").replaceChildren(el("tr", {}, el("th", {}, "When"), el("th", {}, "Who"), el("th", {}, "What")),
          ...entries.map((a) => el("tr", {}, el("td", {}, new Date(a.ts * 1000).toLocaleString()), el("td", {}, a.actor || "—"),
                                   el("td", {}, `${a.action} ${a.target || ""}`))));
      }
    } catch { /* non-admin */ }
  }
  $("#budget-save").addEventListener("click", async () => {
    try {
      await api("/api/settings", { method: "PUT", body: { defaults: { budget: {
        monthly_usd: Number($("#budget-month").value || 0), per_project_usd: Number($("#budget-project").value || 0) } } } });
      toast("Budget saved", "ok");
    } catch (e) { toast(e.message, "error"); }
  });
  $("#roles-save").addEventListener("click", async () => {
    const overrides = {};
    for (const row of $("#roles-table").querySelectorAll("tr[data-role]")) {
      const [m, f] = row.querySelectorAll("input");
      const model = m.value.trim() || m.placeholder, fallback = f.value.trim() || f.placeholder || null;
      if (model !== m.placeholder || fallback !== (f.placeholder || null)) overrides[row.dataset.role] = { model, fallback };
    }
    try { await api("/api/settings", { method: "PUT", body: { defaults: { roles: overrides } } }); toast("Models saved", "ok"); }
    catch (e) { toast(e.message, "error"); }
  });
  onEnter.settings = load;
})();

window.champStudio = { director, templatesView };
