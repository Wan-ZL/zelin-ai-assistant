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
// extra warning line appended to the confirm dialog; `fork` = the button opens
// an explicit multi-choice dialog and each choice carries its own action
// (§41 — mirrors the Mac v0.21 停止 fork and the v0.10.3 拒绝 fork).
const LANES = [
  {
    key: "debt", zh: "潜在任务", en: "Backlog",
    parts: ["debt"],
    // lane help = the shared LaneHelp copy (shared/Sources/Lanes.swift) —
    // webui is JS so the zh strings are mirrored verbatim here (§41).
    help: "真实但不着急的事都先停在这里：雷达低置信度捕获、导入的旧会话、你暂缓的提案。不会自动执行、永不过期；再次提起会自动合并计数。点「研究并提议」升级成提案。",
    actions: [
      { action: "raise", label: "研究并提议" },
      { action: "trash", label: "删除", cls: "danger", confirm: true,
        note: "删除后可在页面底部「回收站」找回。" },
    ],
  },
  {
    key: "needs_approval", zh: "提案", en: "Proposals",
    parts: ["needs_approval"],
    help: "需要你现在拍板的卡：AI 已附上计划、成本和验收标准。批准=后台开始执行；修改=补充方向重提；暂缓=先不做，放进潜在任务。灰色卡是 AI 正在研究的占位。",
    actions: [
      { action: "approve", label: "✅ 批准", cls: "primary" },
      // 拒绝 asks which kind (Mac v0.10.3 parity): trash entries leave
      // merge_or_new matching (the same ask re-raises fresh), 已办完
      // (done_external → delivered) folds later restatements into this thread.
      { label: "❌ 拒绝", cls: "danger", fork: {
          title: "这张卡不需要执行？",
          choices: [
            { label: "不想做（进回收站）", action: "reject", danger: true },
            { label: "已办完（记为已交付）", action: "done_external" },
          ],
        } },
      { action: "comment", label: "💬 修改方向", needs: "修改方向 / 反馈" },
      { action: "defer", label: "暂缓" },
    ],
  },
  {
    key: "running", zh: "运行中", en: "Running",
    parts: ["running", "needs_input"],
    directRun: true,
    help: "已批准的任务由 AI 在后台执行（排队中显示灰卡）。橙色「需输入」= AI 卡住等你回答，排在最前。",
    actions: [
      // §41 parity (Mac v0.21): one 停止 → explicit two-choice fork. 系统外完成
      // left the running card in v0.21 — it lives on the 拒绝 fork instead.
      { label: "⏹ 停止", fork: {
          title: "停止这个任务？",
          message: "退回提案＝丢弃这次结果重来；去待验收＝留下它做的，我来检查",
          choices: [
            { label: "退回提案", action: "abort_execution", danger: true },
            { label: "去待验收", action: "stop_to_review" },
          ],
        } },
    ],
  },
  {
    key: "review", zh: "待验收", en: "Review",
    parts: ["review"],
    help: "AI 认为做完了：看交付摘要或 draft PR。验收=进入「阶段性完成」；打回=带你的反馈继续改。",
    actions: [
      { action: "accept", label: "✓ 验收", cls: "primary" },
      { action: "rework", label: "↩︎ 打回", needs: "打回反馈（必填）" },
    ],
  },
  {
    key: "completed", zh: "阶段性完成", en: "Done for now",
    parts: ["completed"],
    help: "本轮完成——可能还在等对方反馈，可随时退回待验收；确认彻底结束就点「永久完成」。徽章数字是真实总数，列表只显示最近 50 条。",
    actions: [
      { action: "revert_review", label: "退回待验收" },
      { action: "archive", label: "永久完成", confirm: true,
        note: "永久完成后可在页面底部「永久性完成」区放回看板。" },
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

// §41 fork dialog — a native <dialog> replacing window.confirm where the Mac
// shows a multi-choice alert (停止 fork, 拒绝 fork, force-merge primary pick).
// Resolves the chosen `value`, or null on cancel/Esc. Lives on <body>, so the
// 5s board rebuild can't swap it out from under the pointer.
function forkDialog(title, message, choices) {
  return new Promise(resolve => {
    let settled = false;
    const done = v => { if (!settled) { settled = true; resolve(v); } };
    const dlg = document.createElement("dialog");
    dlg.className = "fork";
    const h = el("p", "fork-title");
    h.textContent = title;
    dlg.appendChild(h);
    if (message) {
      const m = el("p", "fork-msg");
      m.textContent = message;
      dlg.appendChild(m);
    }
    const row = el("div", "fork-actions");
    choices.forEach(c => {
      const b = el("button", c.danger ? "danger" : "");
      b.textContent = c.label;
      b.addEventListener("click", () => { done(c.value); dlg.close(); });
      row.appendChild(b);
    });
    const cancel = el("button", "cancel");
    cancel.textContent = "取消";
    cancel.addEventListener("click", () => dlg.close());
    row.appendChild(cancel);
    dlg.appendChild(row);
    dlg.addEventListener("close", () => { done(null); dlg.remove(); });
    document.body.appendChild(dlg);
    dlg.showModal();
  });
}

async function doAction(spec, item) {
  const payload = {};
  if (item && item.id) payload.id = item.id;
  // Name the target so a board refresh mid-aim can't silently swap it.
  const title = String((item && (item.title || item.name || item.id)) || "").slice(0, 80);
  if (spec.fork) {
    const msg = spec.fork.message || (item && item.summary) || title;
    const chosen = await forkDialog(spec.fork.title, msg,
      spec.fork.choices.map(c => ({ label: c.label, value: c.action, danger: c.danger })));
    if (!chosen) return;
    payload.action = chosen;
    postInbox(payload);
    return;
  }
  payload.action = spec.action;
  if (spec.needs) {
    const text = window.prompt(spec.needs + "：");
    if (text == null || !text.trim()) return;
    payload.comment = text.trim();
  } else if (spec.confirm) {
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

// ---------------------------------------------------- merge suggestions (§41)
// The AI merge-suggestion card mirrored on the web (契约 §21/§21bis, same as
// Mac Cards.swift MergeSuggestionCard / iOS CardViews.swift): analyzing / done
// / failed, 接受 (merge_apply) / 取消 (merge_dismiss), and — when the AI did
// NOT land on 「合并」or the analysis failed — 仍然合并 (merge_force with a
// user-chosen primary). Rendered at the top of the 提案 lane.
const MS_VERDICT = {
  merge: "建议合并：副卡并入主卡",
  link_improvement: "建议挂为主卡的改进卡",
  keep_separate: "建议保持独立，不合并",
  close_secondary: "建议关闭副卡（进回收站）",
};
const MS_CONFIDENCE = { high: "置信 高", medium: "置信 中", low: "置信 低" };

function buildTitleIndex(data) {
  const map = {};
  ["debt", "needs_approval", "running", "needs_input", "review", "completed"]
    .forEach(p => {
      (Array.isArray(data[p]) ? data[p] : []).forEach(it => {
        if (it && it.id) map[it.id] = it.title || it.name || it.summary || it.id;
      });
    });
  return id => map[id] || id;
}

function mergeSuggestionEl(s, titleOf) {
  const c = el("div", "card msug");
  const involved = (s.ids || []).map(titleOf).join("  +  ");
  let inner = "";
  if (s.status === "failed") {
    inner += `<p class="title">⚠ 合并分析失败</p>`;
    inner += `<p class="summary dim">${esc(involved)}</p>`;
    if (s.error) inner += `<p class="summary dim">${esc(s.error)}</p>`;
  } else if (s.status === "done") {
    const head = MS_VERDICT[s.verdict] || s.verdict || "分析完成";
    inner += `<p class="title">⤞ ${esc(head)}</p>`;
    if (s.confidence) {
      inner += `<div class="badges"><span class="badge">${esc(MS_CONFIDENCE[s.confidence] || s.confidence)}</span></div>`;
    }
    if (s.primary) {
      inner += `<p class="summary">主卡：${esc(titleOf(s.primary))}</p>`;
      (s.ids || []).filter(i => i !== s.primary).forEach(i => {
        inner += `<p class="summary dim">副卡：${esc(titleOf(i))}</p>`;
      });
    } else {
      (s.ids || []).forEach(i => { inner += `<p class="summary dim">• ${esc(titleOf(i))}</p>`; });
    }
    if (s.rationale) inner += `<p class="summary dim">${esc(s.rationale)}</p>`;
    if (Array.isArray(s.action_plan) && s.action_plan.length) {
      const li = s.action_plan.map(p => `<li>${esc(p)}</li>`).join("");
      inner += `<div class="block"><h4>接受后将执行</h4><ol>${li}</ol></div>`;
    }
  } else { // analyzing
    inner += `<p class="title"><span class="spin"></span> 合并分析中…</p>`;
    inner += `<p class="summary dim">${esc(involved)}</p>`;
  }
  c.innerHTML = inner;

  // Buttons only once the analysis settled (done/failed) — same as Mac/iOS.
  if (s.status === "done" || s.status === "failed") {
    const acts = el("div", "actions");
    if (s.status === "done") {
      const acc = el("button", "primary", "接受");
      acc.addEventListener("click", () => postInbox({ action: "merge_apply", id: s.id }));
      acts.appendChild(acc);
    }
    if (s.status === "failed" || s.verdict !== "merge") {
      const force = el("button", "", "仍然合并");
      force.addEventListener("click", () => forceMerge(s, titleOf));
      acts.appendChild(force);
    }
    const dis = el("button", "", "取消");
    dis.addEventListener("click", () => postInbox({ action: "merge_dismiss", id: s.id }));
    acts.appendChild(dis);
    c.appendChild(acts);
  }
  return c;
}

// 契约 §21bis 强制合并: pick the primary, then merge_force; on success the
// superseded suggestion is dismissed (same follow-up as Mac/iOS).
async function forceMerge(s, titleOf) {
  const ids = s.ids || [];
  if (ids.length < 2) return;
  const choices = ids.map(id => ({
    label: String(titleOf(id) || id).slice(0, 60),
    value: id,
    danger: false,
  }));
  const primary = await forkDialog(
    "强制合并 " + ids.length + " 张卡片 — 选一张作为主卡保留，其余全部并入它",
    "副卡会停止运行、进入「已合并」——不可撤销。来源与交付物保留在主卡上。",
    choices);
  if (!primary) return;
  const ok = await postInbox({ action: "merge_force", ids: ids, primary: primary });
  if (ok) postInbox({ action: "merge_dismiss", id: s.id });
}

// -------------------------------------------------- direct-run capture (§41)
// v0.34 dual input (CONTRACT §34) parity: the Running lane's resident
// direct-run box — type here and it runs now, skipping the proposal gate.
// The draft survives board rebuilds and a failed submit (toast explains).
let directRunDraft = "";
function directRunEl() {
  const wrap = el("div", "runcap");
  const input = document.createElement("input");
  input.type = "text";
  input.autocomplete = "off";
  input.placeholder = "一句话，直接开跑（跳过提案）…";
  input.value = directRunDraft;
  input.addEventListener("input", () => { directRunDraft = input.value; });
  const btn = el("button", "", "开跑");
  const submit = async () => {
    const text = input.value.trim();
    if (!text || btn.disabled) return;
    btn.disabled = true;
    const ok = await postInbox({ action: "capture", text: text, mode: "run" });
    btn.disabled = false;
    if (ok) { directRunDraft = ""; input.value = ""; }
  };
  btn.addEventListener("click", submit);
  input.addEventListener("keydown", e => {
    // IME composition Enter must not submit (same guard as the capture box).
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === "Enter") submit();
  });
  wrap.appendChild(input);
  wrap.appendChild(btn);
  return wrap;
}

// ------------------------------------------------- trash + archived bookends
// §41: the two default-collapsed bookend strips the Mac board grew in v0.33 —
// deleted cards are restorable and sealed cards can be put back RIGHT HERE,
// so the web confirm dialogs no longer claim web can't undo.
function relTime(v) {
  if (v == null || v === "") return "";
  const t = typeof v === "number" ? v * 1000 : Date.parse(v);
  if (isNaN(t)) return "";
  const secs = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (secs < 60) return secs + "s前";
  if (secs < 3600) return Math.round(secs / 60) + "m前";
  if (secs < 86400) return Math.round(secs / 3600) + "h前";
  return Math.round(secs / 86400) + "d前";
}

function actionRow(buttons) {
  const acts = el("div", "actions");
  buttons.forEach(b => {
    const btn = el("button", b.cls || "", esc(b.label));
    if (b.disabled) btn.disabled = true;
    else btn.addEventListener("click", b.onClick);
    acts.appendChild(btn);
  });
  return acts;
}

function trashRowEl(it) {
  const c = el("div", "card");
  let inner = `<div class="id">${esc(it.id || "")}</div>`;
  inner += `<p class="title">${esc(it.title || it.summary || it.id || "(untitled)")}</p>`;
  if (it.summary && it.summary !== it.title) inner += `<p class="summary dim">${esc(it.summary)}</p>`;
  const b = [];
  if (it.permanent) b.push(`<span class="badge">📌 永久</span>`);
  if (it.kind) b.push(`<span class="badge">${esc(it.kind)}</span>`);
  if (it.trash_reason) b.push(`<span class="badge">${esc(String(it.trash_reason).slice(0, 40))}</span>`);
  const ago = relTime(it.trashed_at);
  if (ago) b.push(`<span class="badge">${esc(ago)}</span>`);
  if (b.length) inner += `<div class="badges">${b.join("")}</div>`;
  c.innerHTML = inner;
  const buttons = [
    { label: "恢复", cls: "primary", disabled: !it.id,
      onClick: () => postInbox({ action: "restore", id: it.id }) },
  ];
  if (!it.permanent) {
    buttons.push({ label: "永久保存", disabled: !it.id,
      onClick: () => postInbox({ action: "pin", id: it.id }) });
  }
  c.appendChild(actionRow(buttons));
  return c;
}

function archiveRowEl(it) {
  const c = el("div", "card");
  let inner = `<div class="id">${esc(it.id || "")}</div>`;
  inner += `<p class="title">${esc(it.title || it.summary || it.id || "(untitled)")}</p>`;
  if (it.summary && it.summary !== it.title) inner += `<p class="summary dim">${esc(it.summary)}</p>`;
  const b = [];
  if (it.archive_reason === "user") b.push(`<span class="badge">你封存</span>`);
  else if (it.archive_reason === "auto") b.push(`<span class="badge">自动封存</span>`);
  const ago = relTime(it.archived_at);
  if (ago) b.push(`<span class="badge">${esc(ago)}</span>`);
  if (b.length) inner += `<div class="badges">${b.join("")}</div>`;
  c.innerHTML = inner;
  c.appendChild(actionRow([
    { label: "放回看板", cls: "primary", disabled: !it.id,
      onClick: () => postInbox({ action: "unarchive", id: it.id }) },
  ]));
  return c;
}

const BOOKENDS = [
  { key: "trash", title: "🗑 回收站 · trash", empty: "回收站为空",
    help: "删掉的卡放在这里，「恢复」回到原状态列。回收站条目不参与匹配——同一需求会重新出卡。",
    rows: data => Array.isArray(data.trash) ? data.trash : [],
    row: trashRowEl },
  { key: "archived", title: "🗄 永久性完成 · done for good", empty: "还没有永久完成的卡",
    help: "彻底结束、封存的线程（你点的永久完成 + 自动封存的冷交付）。封存=不再参与匹配，后续相关信息会开新卡而不是回锅这张。可随时「放回看板」回到原状态列。",
    rows: data => Array.isArray(data.archived) ? data.archived : [],
    row: archiveRowEl },
];

function renderBookends(data) {
  const host = document.getElementById("bookends");
  if (!host) return;
  // Keep the user's open/closed state across the rebuild (like 展开详情).
  const open = new Set();
  host.querySelectorAll("details.bookend").forEach(d => {
    if (d.open && d.dataset.key) open.add(d.dataset.key);
  });
  host.innerHTML = "";
  const counts = data.counts || {};
  BOOKENDS.forEach(bk => {
    const rows = bk.rows(data);
    const total = counts[bk.key] != null ? counts[bk.key] : rows.length;
    const d = document.createElement("details");
    d.className = "bookend";
    d.dataset.key = bk.key;
    if (open.has(bk.key)) d.open = true;
    d.appendChild(el("summary", null,
      `<span class="name">${esc(bk.title)}</span><span class="count">${esc(total)}</span>`));
    d.appendChild(el("p", "bookend-help", esc(bk.help)));
    const body = el("div", "bookend-body");
    if (!rows.length) {
      body.appendChild(el("div", "lane-empty", esc(bk.empty)));
    } else {
      rows.forEach(it => body.appendChild(bk.row(it)));
      // archived[] is capped in dashboard.json while counts.archived stays the
      // TRUE total (§2) — say so instead of silently under-showing.
      if (total > rows.length) {
        body.appendChild(el("div", "lane-empty",
          `仅显示最近 ${rows.length} 条（共 ${esc(total)} 条）`));
      }
    }
    d.appendChild(body);
    host.appendChild(d);
  });
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
  const titleOf = buildTitleIndex(data);
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
    // §41: the one-line lane definition every other surface shows (LaneHelp).
    if (lane.help) laneEl.appendChild(el("p", "lane-help", esc(lane.help)));
    const body = el("div", "lane-body");
    // §41: the Running lane hosts the direct-run box (v0.34 dual input).
    if (lane.directRun) body.appendChild(directRunEl());
    // §41: AI merge suggestions at the top of 提案, mirroring Mac/iOS.
    if (lane.key === "needs_approval") {
      (Array.isArray(data.merge_suggestions) ? data.merge_suggestions : [])
        .forEach(s => body.appendChild(mergeSuggestionEl(s, titleOf)));
    }
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
  renderBookends(data);
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
  const submit = async () => {
    const text = input.value.trim();
    if (!text || btn.disabled) return;
    // §41: clear only on confirmed success — a failed submit (daemon down,
    // server error) keeps the typed capture in the field, the toast explains.
    btn.disabled = true;
    const ok = await postInbox({ action: "capture", text: text });
    btn.disabled = false;
    if (ok) input.value = "";
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
