"use strict";
// Local web dashboard front end. Reads state/dashboard.json via /api/dashboard
// (poll every 5s) and acts by POSTing decisions to /api/inbox. Read-only on the
// dashboard; the ONLY mutation path is the inbox write — same contract as the
// Mac app (docs/CONTRACT.md §2/§3/§6/§10). Vanilla JS, no framework, no CDN.

const TOKEN = window.WEBUI_TOKEN || "";
const POLL_MS = 5000;

// The five board lanes (CONTRACT §6). Each maps to dashboard.json partition(s)
// and the actd actions reachable from that lane (§10). `needs` = action prompts
// for text (comment / rework feedback); `confirm` = ask before firing; `note` =
// extra warning line appended to the confirm dialog.
const LANES = [
  {
    key: "debt", zh: "潜在任务", en: "Backlog",
    parts: ["debt"],
    actions: [
      { action: "raise", label: "研究并提议" },
      { action: "trash", label: "删除", cls: "danger", confirm: true,
        note: "删除后在网页端无法恢复（Mac 应用里可以找回）。" },
    ],
  },
  {
    key: "needs_approval", zh: "提案", en: "Proposals",
    parts: ["needs_approval"],
    actions: [
      { action: "approve", label: "✅ 批准", cls: "primary" },
      { action: "reject", label: "❌ 拒绝", cls: "danger", confirm: true },
      { action: "comment", label: "💬 修改方向", needs: "修改方向 / 反馈" },
      { action: "defer", label: "暂缓" },
    ],
  },
  {
    key: "running", zh: "运行中", en: "Running",
    parts: ["running", "needs_input"],
    actions: [
      { action: "stop_to_review", label: "去待验收" },
      { action: "abort_execution", label: "停止·退回", confirm: true },
      { action: "done_external", label: "系统外完成", confirm: true },
    ],
  },
  {
    key: "review", zh: "待验收", en: "Review",
    parts: ["review"],
    actions: [
      { action: "accept", label: "✓ 验收", cls: "primary" },
      { action: "rework", label: "↩︎ 打回", needs: "打回反馈（必填）" },
    ],
  },
  {
    key: "completed", zh: "阶段性完成", en: "Done for now",
    parts: ["completed"],
    actions: [
      { action: "revert_review", label: "退回待验收" },
      { action: "archive", label: "永久完成", confirm: true,
        note: "归档后在网页端无法恢复（Mac 应用里可以找回）。" },
    ],
  },
];

// ------------------------------------------------------------------- helpers
function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function el(tag, cls, html) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
}

let toastTimer = null;
function toast(msg, isErr) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = "toast"; }, 2600);
}

async function api(path, opts) {
  opts = opts || {};
  opts.headers = Object.assign({ "X-Webui-Token": TOKEN }, opts.headers || {});
  const r = await fetch(path, opts);
  return r;
}

async function postInbox(payload) {
  try {
    const r = await api("/api/inbox", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      let msg = "HTTP " + r.status;
      try { const j = await r.json(); if (j.error) msg = j.error; } catch (e) {}
      toast("写入失败：" + msg, true);
      return false;
    }
    toast("已提交：" + payload.action);
    // refresh soon so the board reflects the change after actd consumes it.
    setTimeout(refresh, 800);
    return true;
  } catch (e) {
    toast("网络错误：" + e, true);
    return false;
  }
}

function doAction(spec, item) {
  const payload = { action: spec.action };
  if (item && item.id) payload.id = item.id;
  if (spec.needs) {
    const text = window.prompt(spec.needs + "：");
    if (text == null || !text.trim()) return;
    payload.comment = text.trim();
  } else if (spec.confirm) {
    // Name the target so a board refresh mid-aim can't silently swap it.
    const title = String((item && (item.title || item.name || item.id)) || "").slice(0, 80);
    let msg = "确认 " + spec.label + (title ? "「" + title + "」" : "") + "？";
    if (spec.note) msg += "\n\n" + spec.note;
    if (!window.confirm(msg)) return;
  }
  postInbox(payload);
}

// -------------------------------------------------------------------- render
function freshness(generatedAt) {
  const box = document.getElementById("freshness");
  if (!generatedAt) { box.textContent = "无数据 (no dashboard.json)"; box.className = "freshness stale"; return; }
  const t = Date.parse(generatedAt);
  if (isNaN(t)) { box.textContent = "generated_at: " + generatedAt; box.className = "freshness"; return; }
  const secs = Math.max(0, Math.round((Date.now() - t) / 1000));
  let human;
  if (secs < 60) human = secs + "s ago";
  else if (secs < 3600) human = Math.round(secs / 60) + "m ago";
  else human = Math.round(secs / 3600) + "h ago";
  box.textContent = "更新于 " + human;
  box.className = "freshness" + (secs > 120 ? " stale" : "");
}

function badges(item) {
  const out = [];
  if (item.tier) out.push(`<span class="badge tier">${esc(item.tier)}</span>`);
  if (item.reraised) out.push(`<span class="badge reraised">↩︎ 回锅</span>`);
  if (item.hardness) out.push(`<span class="badge">${esc(item.hardness)}</span>`);
  if (item.deadline) {
    const dl = item.days_left != null ? ` (${item.days_left}d)` : "";
    out.push(`<span class="badge">📅 ${esc(item.deadline)}${esc(dl)}</span>`);
  }
  if (item.repeated) out.push(`<span class="badge">×${esc(item.repeated)}</span>`);
  if (item.show_cost && item.cost_usd != null) out.push(`<span class="badge">$${esc(item.cost_usd)}</span>`);
  if (item.delivery_mode) out.push(`<span class="badge">${esc(item.delivery_mode)}</span>`);
  if (item.target_name) {
    const kind = item.target_kind === "new" ? "🟢 新建" : "🟠 改现有";
    out.push(`<span class="badge">${kind}: ${esc(item.target_name)}</span>`);
  }
  if (item.state === "blocked") {
    out.push(`<span class="badge blocked">⏸ 需输入${item.waiting_for ? ": " + esc(item.waiting_for) : ""}</span>`);
  } else if (item.state === "queued") {
    out.push(`<span class="badge">排队中</span>`);
  } else if (item.state) {
    out.push(`<span class="badge">${esc(item.state)}</span>`);
  }
  const err = item.last_error || item.dispatch_error;
  if (err) out.push(`<span class="badge err">⚠ ${esc(String(err).slice(0, 60))}</span>`);
  return out.join("");
}

function detailsBlock(item) {
  const blocks = [];
  const sources = item.sources || [];
  if (sources.length) {
    const rows = sources.map(s =>
      `<p>${esc(s.who || s.channel || "")}: “${esc(s.quote || "")}”</p>`).join("");
    blocks.push(`<div class="block"><h4>需求来自</h4>${rows}</div>`);
  }
  const plan = item.plan;
  if (Array.isArray(plan) && plan.length) {
    const li = plan.map(p => `<li>${esc(p)}</li>`).join("");
    blocks.push(`<div class="block"><h4>要做什么</h4><ol>${li}</ol></div>`);
  } else if (typeof plan === "string" && plan.trim()) {
    blocks.push(`<div class="block"><h4>要做什么</h4><p>${esc(plan)}</p></div>`);
  }
  const dod = item.dod;
  if (Array.isArray(dod) && dod.length) {
    const li = dod.map(p => `<li>${esc(p)}</li>`).join("");
    blocks.push(`<div class="block"><h4>验收标准</h4><ol>${li}</ol></div>`);
  }
  if (item.final_draft) {
    blocks.push(`<div class="block"><h4>成稿 (FINAL DRAFT)</h4><p>${esc(String(item.final_draft).slice(0, 4000))}</p></div>`);
  }
  if (!blocks.length) return "";
  return `<details class="more"><summary>展开详情 ▸</summary>${blocks.join("")}</details>`;
}

function cardEl(item, lane) {
  const c = el("div", "card");
  c.dataset.id = item.id || "";
  const title = item.title || item.name || item.id || "(untitled)";
  let inner = "";
  inner += `<div class="id">${esc(item.id || "")}</div>`;
  inner += `<p class="title">${esc(title)}</p>`;
  // Review/completed items carry delivered_summary: what was actually
  // delivered is the card body (same as the Mac card), the original ask
  // demoted to a grey context line.
  if (item.delivered_summary) {
    inner += `<p class="summary"><span class="lbl">交付了什么</span>${esc(item.delivered_summary)}</p>`;
    if (item.summary) inner += `<p class="summary dim">${esc(item.summary)}</p>`;
  } else if (item.summary) {
    inner += `<p class="summary">${esc(item.summary)}</p>`;
  }
  if (item.reraised_note) inner += `<p class="summary">新增: ${esc(item.reraised_note)}</p>`;
  const b = badges(item);
  if (b) inner += `<div class="badges">${b}</div>`;
  inner += detailsBlock(item);
  c.innerHTML = inner;

  const acts = el("div", "actions");
  lane.actions.forEach(spec => {
    const btn = el("button", spec.cls || "", esc(spec.label));
    if (!item.id) btn.disabled = true; // every req-level action needs an id
    // AI-processing (raising) cards: approve/reject/defer are guarded no-ops
    // daemon-side (audit 2026-07-14) — don't offer a button that toasts
    // success and does nothing. 修改方向 stays live (it folds honestly), same
    // as the iOS card body does.
    if (item.processing && spec.action !== "comment") btn.disabled = true;
    btn.addEventListener("click", () => doAction(spec, item));
    acts.appendChild(btn);
  });
  c.appendChild(acts);
  return c;
}

// Rebuilding the board swaps the DOM under the cursor, so an aimed click can
// land on a different card. Guard: only rebuild when the data actually changed
// (generated_at ticks every poll, so it is excluded from the comparison), and
// never while a pointer is held down — defer to just after release.
let lastBoardKey = null;
let pendingData = null;
let pointerHeld = false;
window.addEventListener("pointerdown", () => { pointerHeld = true; }, true);
window.addEventListener("pointerup", flushRender, true);
window.addEventListener("pointercancel", flushRender, true);
function flushRender() {
  pointerHeld = false;
  if (pendingData) {
    const d = pendingData;
    pendingData = null;
    // setTimeout so the click event for this release dispatches first.
    setTimeout(() => render(d), 0);
  }
}

function render(data) {
  freshness(data.generated_at);
  if (pointerHeld) { pendingData = data; return; }
  const key = JSON.stringify(data, (k, v) => (k === "generated_at" ? undefined : v));
  if (key === lastBoardKey) return;
  lastBoardKey = key;
  const board = document.getElementById("board");
  // Keep the user's 展开详情 open across the rebuild.
  const openIds = new Set();
  board.querySelectorAll(".card").forEach(c => {
    const d = c.querySelector("details");
    if (d && d.open && c.dataset.id) openIds.add(c.dataset.id);
  });
  board.innerHTML = "";
  LANES.forEach(lane => {
    const items = [];
    lane.parts.forEach(p => {
      const arr = data[p];
      if (Array.isArray(arr)) items.push(...arr);
    });
    const counts = data.counts || {};
    // prefer the authoritative count for the primary partition when present.
    const count = (lane.parts.length === 1 && counts[lane.parts[0]] != null)
      ? counts[lane.parts[0]] : items.length;

    const laneEl = el("div", "lane");
    laneEl.appendChild(el("div", "lane-head",
      `<span class="name">${esc(lane.zh)} · ${esc(lane.en)}</span>` +
      `<span class="count">${esc(count)}</span>`));
    const body = el("div", "lane-body");
    if (!items.length) {
      body.appendChild(el("div", "lane-empty", "空"));
    } else {
      items.forEach(it => {
        const card = cardEl(it, lane);
        if (it.id && openIds.has(String(it.id))) {
          const d = card.querySelector("details");
          if (d) d.open = true;
        }
        body.appendChild(card);
      });
    }
    laneEl.appendChild(body);
    board.appendChild(laneEl);
  });
}

// --------------------------------------------------------------------- poll
let refreshing = false;
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const r = await api("/api/dashboard");
    if (!r.ok) { toast("读取看板失败：HTTP " + r.status, true); return; }
    const data = await r.json();
    render(data || {});
  } catch (e) {
    toast("读取看板失败：" + e, true);
  } finally {
    refreshing = false;
  }
}

// ------------------------------------------------------------------ capture
function wireCapture() {
  const input = document.getElementById("captureInput");
  const btn = document.getElementById("captureBtn");
  const submit = () => {
    const text = input.value.trim();
    if (!text) return;
    postInbox({ action: "capture", text: text });
    input.value = "";
  };
  btn.addEventListener("click", submit);
  input.addEventListener("keydown", e => {
    // The Enter that confirms an IME composition (pinyin etc.) must not
    // submit — isComposing covers Chrome, keyCode 229 covers Safari.
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === "Enter") submit();
  });
}

wireCapture();
refresh();
setInterval(refresh, POLL_MS);
