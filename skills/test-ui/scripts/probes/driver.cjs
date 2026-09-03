#!/usr/bin/env node
// test-ui skill · runtime probe. Runs under the PROJECT's playwright (path passed in the config; the skill
// never installs anything). One process per run: for every screen × scene × theme × viewport × language × flags
// it opens the page, freezes clocks/animations, waits for `ready`, then reads: accessibility-ish nodes (role,
// accessible name, visibility, focusability, bbox, computed colors/fonts, effective background → contrast),
// landmarks with side relative to <main>, a Tab walk, horizontal overflow, computed values of every declared
// CSS custom property, geometry bboxes for the mapped selectors/roles, the first-frame theme under both
// prefers-color-scheme emulations, optional axe-core violations, and a masked screenshot.
// Output: one JSON file (config.out) = {tool, dims, runs:[…]} parsed by inventory_a11y.parse_runtime.
// CommonJS on purpose: `require(config.playwright)` works from any cwd (ESM ignores NODE_PATH).
// Law pointers: CONTRACT §UI-parity (parity contract; demo seed only — the Python side refuses to run this without
// the demo marker), §58 (thresholds read-only). Design: vnext2-plan R2.8 / D14.
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const cfgPath = process.argv[2];
if (!cfgPath) { console.error("driver.cjs <config.json>"); process.exit(2); }
const cfg = JSON.parse(fs.readFileSync(cfgPath, "utf8"));
const pw = require(cfg.playwright);

const FREEZE_CSS = "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important;scroll-behavior:auto!important}";

// Everything below `PAGE_SCRIPT` runs inside the page.
const PAGE_SCRIPT = `(() => {
  const INTERACTIVE = new Set(["button","link","checkbox","radio","switch","textbox","searchbox","combobox","listbox","option","slider","spinbutton","tab","menuitem","menuitemcheckbox","menuitemradio","treeitem"]);
  const LANDMARK = new Set(["banner","navigation","main","complementary","contentinfo","region","search","form"]);
  const IMPLICIT = {button:"button",a:"link",select:"combobox",textarea:"textbox",h1:"heading",h2:"heading",h3:"heading",h4:"heading",h5:"heading",h6:"heading",nav:"navigation",header:"banner",main:"main",footer:"contentinfo",aside:"complementary",ul:"list",ol:"list",li:"listitem",img:"img",dialog:"dialog",table:"table",tr:"row",td:"cell",th:"cell",hr:"separator",form:"form",summary:"button",output:"status",progress:"status"};
  const INPUT = {checkbox:"checkbox",radio:"radio",range:"slider",number:"spinbutton",search:"searchbox",button:"button",submit:"button",reset:"button",image:"button"};
  const roleOf = (el) => {
    const explicit = (el.getAttribute("role") || "").split(" ")[0];
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === "input") { const t = (el.getAttribute("type") || "text").toLowerCase(); return t === "hidden" ? null : (INPUT[t] || "textbox"); }
    if (tag === "a" && !el.hasAttribute("href")) return null;
    if (tag === "section" && (el.hasAttribute("aria-label") || el.hasAttribute("aria-labelledby"))) return "region";
    return IMPLICIT[tag] || null;
  };
  const text = (el) => (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim();
  const nameOf = (el, role) => {
    const label = el.getAttribute("aria-label"); if (label) return [label.trim(), "aria-label"];
    const by = el.getAttribute("aria-labelledby");
    if (by) { const parts = by.split(/\\s+/).map((id) => { const t = document.getElementById(id); return t ? text(t) : ""; }); return [parts.join(" ").trim(), "aria-labelledby"]; }
    if (role === "img") return [el.getAttribute("alt") || "", "alt"];
    if (el.labels && el.labels.length) return [text(el.labels[0]), "label"];
    if (LANDMARK.has(role) || role === "list") return ["", "none"];
    const t = text(el); if (t) return [t, "text"];
    return [el.getAttribute("title") || "", el.getAttribute("title") ? "title" : "none"];
  };
  const hiddenBy = (el) => {
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      if (n.getAttribute("aria-hidden") === "true") return "aria-hidden";
      if (n.hasAttribute("hidden")) return "hidden";
      const cs = getComputedStyle(n);
      if (cs.display === "none") return "display:none";
      if (cs.visibility === "hidden") return "visibility:hidden";
    }
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return "0x0";
    // off-canvas = outside the DOCUMENT's scrollable box (the left:-9999px family), not merely below the fold:
    // an element at y > innerHeight on a scrolling page is still in the accessibility tree and one scroll away.
    const de = document.documentElement, sx = window.scrollX || 0, sy = window.scrollY || 0;
    if (r.right + sx < 0 || r.bottom + sy < 0 || r.left + sx > de.scrollWidth || r.top + sy > de.scrollHeight) return "off-canvas";
    return null;
  };
  const focusable = (el) => {
    if (el.hasAttribute("disabled") || el.closest("[inert]")) return false;
    const ti = el.getAttribute("tabindex"); if (ti !== null) return parseInt(ti, 10) >= 0;
    return ["a","button","input","select","textarea","summary"].includes(el.tagName.toLowerCase()) || el.isContentEditable;
  };
  const parseColor = (s) => { const m = /rgba?\\(([^)]+)\\)/.exec(s || ""); if (!m) return null; const p = m[1].split(/[,\\s\\/]+/).filter(Boolean).map(Number); return {r:p[0],g:p[1],b:p[2],a:p.length > 3 ? p[3] : 1}; };
  // effective background = every ancestor's background composited bottom-up until opaque (a 12 % tinted chip over the
  // page is NOT the chip color — taking the first non-transparent layer as-is made text == background, ratio 1).
  const over = (top, bot) => { const a = top.a + bot.a * (1 - top.a); if (a <= 0) return {r:0,g:0,b:0,a:0}; return {r: (top.r * top.a + bot.r * bot.a * (1 - top.a)) / a, g: (top.g * top.a + bot.g * bot.a * (1 - top.a)) / a, b: (top.b * top.a + bot.b * bot.a * (1 - top.a)) / a, a}; };
  const effectiveBg = (el) => { let acc = {r:0,g:0,b:0,a:0}; for (let n = el; n; n = n.parentElement) { const c = parseColor(getComputedStyle(n).backgroundColor); if (!c || c.a <= 0) continue; acc = over(acc, c); if (acc.a >= 0.999) return acc; } return over(acc, {r:255,g:255,b:255,a:1}); };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const composite = (fg, bg) => ({r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1});
  const hex = (c) => "#" + [c.r, c.g, c.b, Math.round(c.a * 255)].map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
  const contrast = (el, cs) => { let fg = parseColor(cs.color); const bg = effectiveBg(el); if (!fg) return null; if (fg.a < 1) fg = composite(fg, bg); const l1 = lum(fg), l2 = lum(bg); const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05); const size = parseFloat(cs.fontSize); const bold = parseInt(cs.fontWeight, 10) >= 700; return {ratio: Math.round(ratio * 100) / 100, against: hex(bg), large: size >= 24 || (size >= 18.66 && bold)}; };
  const mainRect = (document.querySelector("main, [role=main]") || document.body).getBoundingClientRect();
  const sideOf = (r) => { if (r.right <= mainRect.left + 2) return "left"; if (r.left >= mainRect.right - 2) return "right"; if (r.bottom <= mainRect.top + 2) return "top"; if (r.top >= mainRect.bottom - 2) return "bottom"; return "inside"; };
  const landmarkPath = (el) => { const parts = []; for (let n = el.parentElement; n; n = n.parentElement) { const r = roleOf(n); if (r && (LANDMARK.has(r) || r === "list" || r === "region" || r === "tablist")) parts.unshift(r + ":" + (nameOf(n, r)[0] || r).toLowerCase().replace(/[^a-z0-9\\u4e00-\\u9fff]+/g, "-").replace(/^-|-$/g, "")); } return ["window"].concat(parts).join(">"); };
  const nodes = [], landmarks = [], orders = {};
  let idx = 0;
  for (const el of document.querySelectorAll("body *")) {
    const role = roleOf(el); if (!role || el.closest("svg") ) continue;
    const [name, nameSource] = nameOf(el, role);
    const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
    const parent = landmarkPath(el); orders[parent] = (orders[parent] || 0) + 1;
    el.setAttribute("data-tu-idx", String(idx));
    const tag = el.tagName.toLowerCase();
    const node = {role, name, name_source: nameSource, text: text(el).slice(0, 200), parent, order: orders[parent] - 1,
      side: LANDMARK.has(role) ? sideOf(r) : null, visible: hiddenBy(el) === null, hidden_by: hiddenBy(el), focusable: focusable(el),
      tab_index: null, idx, pin: el.getAttribute("data-parity-id"), level: /^h[1-6]$/.test(tag) ? parseInt(tag[1], 10) : (el.getAttribute("aria-level") ? parseInt(el.getAttribute("aria-level"), 10) : null),
      bbox: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
      computed: {color: cs.color, "background-color": cs.backgroundColor, font: {weight: parseInt(cs.fontWeight, 10) || 400, size: parseFloat(cs.fontSize), line: parseFloat(cs.lineHeight) || null, family: /mono/i.test(cs.fontFamily) ? "mono" : "sans"}, "border-radius": parseFloat(cs.borderRadius) || 0, padding: [parseFloat(cs.paddingTop) || 0, parseFloat(cs.paddingLeft) || 0]},
      contrast: INTERACTIVE.has(role) || role === "heading" || role === "static" ? contrast(el, cs) : null};
    nodes.push(node);
    if (LANDMARK.has(role) || role === "list" || role === "region" || role === "tablist") landmarks.push({role, name, parent, order: node.order, side: sideOf(r), bbox: node.bbox, children: []});
    idx += 1;
  }
  const tokens = {}; const root = getComputedStyle(document.documentElement);
  for (const v of (window.__tuTokens || [])) { const val = root.getPropertyValue(v).trim(); if (val) tokens[v] = val; }
  const geometry = {};
  for (const [pathKey, spec] of Object.entries(window.__tuGeometry || {})) {
    if (spec.screen && spec.screen !== "*" && spec.screen !== window.__tuScreen) continue;
    let els = spec.selector ? document.querySelectorAll(spec.selector) : Array.from(document.querySelectorAll("body *")).filter((e) => roleOf(e) === spec.role);
    let rects = Array.from(els).map((e) => e.getBoundingClientRect()).filter((b) => b.width > 0);
    // fallback: the elements whose CSS rule consumes the token (selectors derived from the project's component CSS)
    if (!rects.length && spec.css_selectors && spec.css_selectors.length) {
      try { els = document.querySelectorAll(spec.css_selectors.join(",")); rects = Array.from(els).map((e) => e.getBoundingClientRect()).filter((b) => b.width > 0); } catch (_) { /* invalid selector → unmeasured */ }
    }
    if (spec.measure === "gap") { const xs = rects.map((b) => b.left).sort((a, b) => a - b); geometry[pathKey] = xs.slice(1).map((x, i) => Math.round(x - rects[i].right)); }
    else geometry[pathKey] = rects.map((b) => Math.round(spec.measure === "height" ? b.height : b.width));
  }
  const de = document.documentElement;
  return {nodes, landmarks, tokens, geometry, lang: de.getAttribute("lang") || "", overflow: {scrollWidth: de.scrollWidth, clientWidth: de.clientWidth},
    observed_theme: de.dataset.theme || (getComputedStyle(de).colorScheme.includes("dark") ? "dark" : "light")};
})()`;

function sha256(file) { return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex"); }

// Tab walk: stop on wrap-around (an inventoried element seen twice) or when focus stops moving (end of document /
// focus trap). A focused element that is NOT in the inventory (a generic focusable <div>) is skipped, never a stop —
// otherwise every control after the first such element would be reported unreachable.
async function tabWalk(page, limit) {
  const seen = []; let skipped = 0;
  await page.evaluate(() => { document.activeElement && document.activeElement.blur && document.activeElement.blur(); });
  for (let i = 0; i < limit; i += 1) {
    await page.keyboard.press("Tab");
    const step = await page.evaluate(() => {
      const a = document.activeElement; if (!a || a === document.body) return {idx: null, same: false, body: true};
      const same = a.hasAttribute("data-tu-last");
      for (const e of document.querySelectorAll("[data-tu-last]")) e.removeAttribute("data-tu-last");
      a.setAttribute("data-tu-last", "1");
      return {idx: a.getAttribute("data-tu-idx"), same, body: false};
    });
    if (step.same) break;
    if (step.idx === null) { skipped += 1; if (skipped > 100) break; continue; }
    if (seen.includes(step.idx)) break;
    seen.push(step.idx);
  }
  return seen;
}

async function paintMasks(page, masks) {
  await page.evaluate((rects) => {
    for (const [x, y, w, h] of rects) { const d = document.createElement("div"); d.setAttribute("data-tu-mask", "1");
      d.style.cssText = `position:fixed;left:${x}px;top:${y}px;width:${w}px;height:${h}px;background:#ff00ff;z-index:2147483647;pointer-events:none`; document.body.appendChild(d); }
  }, masks);
}

async function runOne(browser, dim, tokenVars) {
  const context = await browser.newContext({viewport: {width: dim.viewport.w, height: dim.viewport.h}, colorScheme: dim.emulation || "light", locale: dim.language === "zh" ? "zh-CN" : "en-US", reducedMotion: "reduce", deviceScaleFactor: cfg.dpr || 1});
  const page = await context.newPage();
  await page.clock.install({time: new Date("2026-01-01T00:00:00Z")}).catch(() => {});
  await page.addInitScript(({theme, lang, tKey, lKey, setTheme}) => {
    try { if (setTheme) localStorage.setItem(tKey, theme); else localStorage.removeItem(tKey); localStorage.setItem(lKey, lang); } catch (_) {}
  }, {theme: dim.theme, lang: dim.language, tKey: cfg.theme_storage_key, lKey: cfg.lang_storage_key, setTheme: !dim.observe_default});
  await page.goto(cfg.url + (dim.screen.route || ""), {waitUntil: "load"});
  await page.addStyleTag({content: FREEZE_CSS});
  if (cfg.ready && cfg.ready.startsWith("/")) { /* http ready already probed by python */ } else if (cfg.ready) { await page.waitForSelector(cfg.ready, {timeout: 20000}); }
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.evaluate(({tokens, geometry, screen}) => { window.__tuTokens = tokens; window.__tuGeometry = geometry; window.__tuScreen = screen; }, {tokens: tokenVars, geometry: cfg.geometry || {}, screen: dim.screen.id});
  const snap = await page.evaluate(PAGE_SCRIPT);
  const run = Object.assign({screen: dim.screen.id, scene: dim.scene, theme: dim.theme, viewport: dim.viewport.name, language: dim.language, flags: dim.flags, emulation: dim.emulation}, snap);
  run.focus_walk = (await tabWalk(page, cfg.tab_limit || 400)).map((i) => Number(i)); // node idx; Python maps idx → item id
  if (cfg.axe) { try { await page.addScriptTag({path: cfg.axe}); const res = await page.evaluate(async () => (await window.axe.run(document, {resultTypes: ["violations"]})).violations);
    run.axe = res.map((v) => ({id: v.id, impact: v.impact, help: v.help, target: (v.nodes[0] && v.nodes[0].target || []).join(" ")})); } catch (e) { run.axe_error = String(e); } }
  const masks = (cfg.masks || {})[dim.screen.id] || [];
  if (masks.length) await paintMasks(page, masks);
  if (!dim.observe_default) {
    const file = path.join(cfg.shots_dir, `${dim.screen.id}__${dim.scene}__${dim.theme}__${dim.viewport.name}__${dim.language}.png`);
    fs.mkdirSync(cfg.shots_dir, {recursive: true});
    await page.screenshot({path: file, fullPage: false, animations: "disabled", caret: "hide"});
    run.shot = file; run.shot_sha256 = sha256(file); run.masks = masks;
    run.masked_ratio = masks.reduce((acc, m) => acc + (m[2] * m[3]), 0) / (dim.viewport.w * dim.viewport.h);
  }
  await context.close();
  return run;
}

function dims() {
  const out = [];
  const scenes = cfg.scenes || ["initial"];
  for (const screen of cfg.screens || [{id: "index", route: ""}]) for (const scene of scenes) for (const theme of cfg.themes || ["light"]) for (const viewport of cfg.viewports || [{name: "desktop", w: 1440, h: 900}]) for (const language of cfg.languages || ["zh"]) for (const flags of cfg.flags_all_on ? ["default", "all_on"] : ["default"]) {
    out.push({screen, scene, theme, viewport, language, flags, emulation: theme === "dark" ? "dark" : "light"});
  }
  // first-frame default theme under both emulations, no stored preference (theme_default_observed)
  const first = (cfg.screens || [{id: "index", route: ""}])[0];
  for (const emulation of ["light", "dark"]) out.push({screen: first, scene: scenes[0], theme: "unset", viewport: (cfg.viewports || [{name: "desktop", w: 1440, h: 900}])[0], language: (cfg.languages || ["zh"])[0], flags: "default", emulation, observe_default: true});
  return out;
}

(async () => {
  const browser = await pw.chromium.launch({headless: true});
  const runs = [];
  try {
    for (const dim of dims()) {
      if (dim.flags === "all_on") continue; // flags_all_on needs the Python side to relaunch with overrides; recorded as a gap in v0.1
      runs.push(await runOne(browser, dim, cfg.tokens || []));
    }
  } finally { await browser.close(); }
  const version = (() => { try { return require(path.join(path.dirname(cfg.playwright), "package.json")).version; } catch (_) { return "unknown"; } })();
  fs.writeFileSync(cfg.out, JSON.stringify({tool: "playwright " + version, dims: {themes: cfg.themes, viewports: cfg.viewports, languages: cfg.languages, scenes: cfg.scenes, default_theme: (cfg.themes || ["light"])[0]}, runs}, null, 1));
  process.exit(0);
})().catch((err) => { console.error(String(err && err.stack || err)); process.exit(1); });
