// UI audit: crawl every app view (desktop + phone), the editor (three window sizes, panels,
// dialogs) and the review page. Writes screenshots and report.json, prints a summary and exits
// non-zero when any console error, failed request or visual issue is found.
//
//   node scripts/ui-audit/run.mjs [--base http://localhost:8787] [--out ui-audit-out] [--project <editor project id>]
//
// Needs Node 22+ and Chrome (set CHROME=/path/to/chrome if it isn't in the usual place).

import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { HEURISTICS, launch, sleep } from "./cdp.mjs";

const arg = (name, def) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : def;
};
const BASE = arg("base", "http://localhost:8787");
const OUT = resolve(arg("out", "ui-audit-out"));
mkdirSync(`${OUT}/shots`, { recursive: true });
const VIEWS = ["director", "templates", "projects", "brands", "create", "animate", "lab", "library", "settings"];
const page = await launch(OUT);
const report = [];

async function check(name) {
  await sleep(800);
  const heur = await page.js(HEURISTICS);
  await page.shot(`${OUT}/shots/${name}.png`);
  report.push({ name, errors: page.drain(), ...heur });
}

// ── main app ──
for (const [label, w, h, mobile] of [["desktop", 1440, 900, false], ["phone", 390, 844, true]]) {
  await page.size(w, h, mobile);
  for (const v of VIEWS) {
    await page.goto(`${BASE}/#/${v}`, 300);
    await page.js("location.reload(), true");
    await sleep(3000);
    await check(`${label}-${v}`);
    if (label === "phone" && v === "director") {
      await page.js(`document.querySelector('#nav-toggle')?.click(), true`);
      await check("phone-menu-open");
      await page.js(`document.querySelector('#nav-toggle')?.click(), true`);
    }
  }
}

// ── editor ──
let pid = arg("project");
if (!pid) {
  const list = await (await fetch(`${BASE}/api/projects?kind=edit`)).json();
  pid = list.projects?.[0]?.id;
}
if (pid) {
  for (const [w, h] of [[1680, 1000], [1280, 720], [1024, 640], [800, 600]]) {
    await page.size(w, h);
    await page.goto(`${BASE}/editor/?project=${pid}`, 4500);
    await check(`editor-${w}`);
  }
  await page.size(1680, 1000);
  await page.goto(`${BASE}/editor/?project=${pid}`, 4500);
  page.drain();
  const clickText = (sel, text) =>
    page.js(`(() => { const b = [...document.querySelectorAll(${JSON.stringify(sel)})].find(e => e.textContent.trim().startsWith(${JSON.stringify(text)}) || e.getAttribute('aria-label') === ${JSON.stringify(text)}); if (b) b.click(); return !!b; })()`);
  for (const tab of ["Essential Graphics", "Comments", "Project"]) {
    await clickText(".panel-tabs .tab", tab);
    await check(`editor-tab-${tab.split(" ")[0].toLowerCase()}`);
  }
  for (const btn of ["Versions", "Keyboard shortcuts", "Export"]) {
    await clickText(".topbar button", btn);
    await check(`editor-dialog-${btn.split(" ")[0].toLowerCase()}`);
    await page.key("Escape", "Escape", 27);
    await sleep(300);
  }
} else console.log("no editor project found — skipping the editor (pass --project <id>)");

// ── review page error states ──
await page.size(1280, 800);
await page.goto(`${BASE}/review.html`, 2000);
await check("review-no-token");

writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 1));
let problems = 0;
for (const r of report) {
  const issues = r.issues ?? [];
  const bad = r.errors.length + issues.length + (r.overflowX ? 1 : 0);
  problems += bad;
  console.log(`${bad ? "✗" : "✓"} ${r.name.padEnd(26)} errors=${r.errors.length} issues=${issues.length}${r.overflowX ? ` overflowX (${r.docW}>${r.vw})` : ""}`);
  for (const e of r.errors) console.log(`    ${e}`);
  for (const i of issues) console.log(`    ${JSON.stringify(i)}`);
}
console.log(`\n${report.length} states, ${problems} problem(s). Screenshots: ${OUT}/shots`);
page.close();
process.exit(problems ? 1 : 0);
