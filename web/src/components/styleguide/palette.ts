// 老 app ↔ 新 token 对照表数据（styleguide 第 1 节「老 app 参照区」的唯一数据源）。
// 「老 app 色值」列 = docs/design/vnext.md §10 Theme 提取表（macOS dark 系统色解析值 +
// owner 截图基准），并经 mac/Sources/Cards.swift tint 逐处复核（行注记标出行号出处）。
// 例外声明（CONVENTIONS §5）：本文件的字面 hex 是【历史参照数据】不是 UI 样式——
// 新侧渲染一律走 var(--token)；老侧色块必须显示老值本身，token 化反而失真。
// flagged=true ⇒ 老 vs 新仍有可见差异（色相或位置）；owner 验收单（绿批准/红拒绝/蓝紫修改/
// 灰暂缓/绿验收/橙打回/青复制成稿；粉紫T1章/紫交付/绿已交付/橙需输入/红受阻/黄等待/
// 紫被提及已并入）已全部按 hue 一比一落地——见各行 token 列。

export type Sample =
  | { kind: "button"; className: string; zh: string; en: string }
  | { kind: "chip"; className: string; zh: string; en: string }
  | { kind: "chips"; className: string; labels: string[] }
  | { kind: "dots" }
  | { kind: "layers" }
  | { kind: "text"; zh: string; en: string };

export interface PaletteRow {
  key: string;
  zh: string;
  en: string;
  /** 老 app 色值（文本，含 SwiftUI 名与解析 hex） */
  oldValue: string;
  /** 老值色块（字面 hex；[] = 老 app 无对应色） */
  oldSwatches: string[];
  /** 新 token 名（可多个，文本） */
  token: string;
  /** 新色值（dark / light 文本，值抄自 tokens.css） */
  newDark: string;
  newLight: string;
  /** 新值活色块（var(--x)，随主题切换） */
  swatchVars: string[];
  sample: Sample;
  flagged: boolean;
  noteZh?: string;
  noteEn?: string;
}

export const PALETTE_ROWS: PaletteRow[] = [
  {
    key: "approve", zh: "批准（approve）", en: "Approve",
    oldValue: ".green · #32d74b", oldSwatches: ["#32d74b"],
    token: "--success（.btn-success）", newDark: "#32d74b", newLight: "#218739", swatchVars: ["--success"],
    sample: { kind: "button", className: "btn btn-success", zh: "批准", en: "Approve" }, flagged: false,
    noteZh: "owner 验收单回归：绿批准（曾走 teal btn-primary，已按 Mac tint 改回绿）。",
    noteEn: "Restored per owner checklist: green Approve (was teal btn-primary; back to Mac's green tint).",
  },
  {
    key: "accept", zh: "验收（accept）", en: "Accept",
    oldValue: ".green · #32d74b", oldSwatches: ["#32d74b"],
    token: "--success（.btn-success）", newDark: "#32d74b", newLight: "#218739", swatchVars: ["--success"],
    sample: { kind: "button", className: "btn btn-success", zh: "验收", en: "Accept" }, flagged: false,
    noteZh: "同批准：绿验收。", noteEn: "Same as Approve: green Accept.",
  },
  {
    key: "reject", zh: "拒绝 / 危险（reject）", en: "Reject / danger",
    oldValue: ".red · #ff453a", oldSwatches: ["#ff453a"],
    token: "--danger（.btn-danger / .chip-danger）", newDark: "#ff453a", newLight: "#d70015", swatchVars: ["--danger"],
    sample: { kind: "button", className: "btn btn-danger", zh: "拒绝", en: "Reject" }, flagged: false,
  },
  {
    key: "comment", zh: "修改 / 评论（comment）", en: "Comment / modify",
    oldValue: ".blue（macOS dark 解析 #0a84ff）", oldSwatches: ["#0a84ff"],
    token: "--info（.btn-info）", newDark: "#0a84ff", newLight: "#0a66cc", swatchVars: ["--info"],
    sample: { kind: "button", className: "btn btn-info", zh: "修改", en: "Comment" }, flagged: false,
    noteZh: "blue 槽位已补（--info = systemBlue 家族，owner 验收单称「蓝紫」）；DebtRow 研究并提议同色。",
    noteEn: "Blue slot added (--info = systemBlue family; the owner checklist calls it blue-violet); DebtRow's raise shares it.",
  },
  {
    key: "later", zh: "暂缓（defer）", en: "Later",
    oldValue: ".gray tint（systemGray · #98989d，v0.18 起）", oldSwatches: ["#98989d"],
    token: "中性 .btn", newDark: "#23262e 面", newLight: "#ffffff 面", swatchVars: ["--surface"],
    sample: { kind: "button", className: "btn", zh: "暂缓", en: "Later" }, flagged: false,
    noteZh: "更正前版表记：Mac ApprovalCardView 四按钮含暂缓（Cards.swift:1032 .tint(.gray)），灰对灰一致。",
    noteEn: "Corrects the earlier claim: Mac's four-verb row includes Later (Cards.swift:1032, .tint(.gray)); grey matches grey.",
  },
  {
    key: "sendback", zh: "打回（rework）", en: "Send back",
    oldValue: ".orange · #ff9f0a", oldSwatches: ["#ff9f0a"],
    token: "--warning（.btn-warning）", newDark: "#ff9f0a", newLight: "#c05d00", swatchVars: ["--warning"],
    sample: { kind: "button", className: "btn btn-warning", zh: "打回", en: "Send Back" }, flagged: false,
    noteZh: "橙打回回归（回答…/停止 同橙，Mac tint 一致）。",
    noteEn: "Orange Send Back restored (Answer… and Stop share the orange, matching Mac tints).",
  },
  {
    key: "copydraft", zh: "复制成稿（copy draft）", en: "Copy final draft",
    oldValue: ".teal · #6ac4dc", oldSwatches: ["#6ac4dc"],
    token: "--accent（.btn-accent 描边）", newDark: "#6ac4dc", newLight: "#12758c", swatchVars: ["--accent"],
    sample: { kind: "button", className: "btn btn-accent", zh: "复制成稿", en: "Copy final draft" }, flagged: false,
    noteZh: "青复制成稿回归。", noteEn: "Teal Copy-final-draft restored.",
  },
  {
    key: "stop", zh: "停止（abort / stop）", en: "Stop",
    oldValue: ".orange · #ff9f0a（停止并退回）", oldSwatches: ["#ff9f0a"],
    token: "--warning（.btn-warning；危险分支在 fork 弹窗内）", newDark: "#ff9f0a", newLight: "#c05d00", swatchVars: ["--warning"],
    sample: { kind: "button", className: "btn btn-warning", zh: "停止", en: "Stop" }, flagged: false,
    noteZh: "橙停止回归；web 仍把停止拆成 fork 弹窗（退回提案=danger / 去待验收）——结构差异，非色差。",
    noteEn: "Orange Stop restored; web still splits Stop into a fork dialog (discard = danger / keep for review) — structural, not a hue difference.",
  },
  {
    key: "backreview", zh: "退回待验收（revert_review）", en: "Back to review",
    oldValue: ".teal · #6ac4dc", oldSwatches: ["#6ac4dc"],
    token: "--accent（.btn-accent）", newDark: "#6ac4dc", newLight: "#12758c", swatchVars: ["--accent"],
    sample: { kind: "button", className: "btn btn-accent", zh: "退回待验收", en: "Back to review" }, flagged: false,
  },
  {
    key: "tier", zh: "tier chips（T0/T1/T2，粉紫章）", en: "Tier chips (T0/T1/T2, pink-magenta)",
    oldValue: ".purple · #bf5af2（三档同色）", oldSwatches: ["#bf5af2"],
    token: "--purple（.chip-purple）", newDark: "#bf5af2", newLight: "#9440d6", swatchVars: ["--purple"],
    sample: { kind: "chips", className: "chip chip-purple", labels: ["T0", "T1 · 需要批准", "T2 · 键入确认"] }, flagged: false,
    noteZh: "粉紫回归（owner 验收单：粉紫T1章；曾误走 teal）；三档同色靠文字区分，与 Mac 一致。",
    noteEn: "Pink-magenta restored (owner checklist; was wrongly teal); tiers share one color, text tells them apart, same as Mac.",
  },
  {
    key: "delivertag", zh: "交付 tag（聊天成稿 badge）", en: "Deliver tag (chat draft badge)",
    oldValue: "源码 .blue #0a84ff（Cards.swift:1214）· §10 提取表记 .purple #bf5af2", oldSwatches: ["#0a84ff", "#bf5af2"],
    token: "--purple（.chip-purple）", newDark: "#bf5af2", newLight: "#9440d6", swatchVars: ["--purple"],
    sample: { kind: "chip", className: "chip chip-purple", zh: "交付：聊天成稿", en: "Deliver: chat draft" }, flagged: true,
    noteZh: "源码 Badge 是 .blue，但 §10 提取表与 owner 验收单（紫交付）拍板紫——按紫实现；⚠️ 保留记录源码差异。",
    noteEn: "Source badge is .blue, but the §10 extraction map and the owner checklist (purple deliver) ratify purple — implemented purple; ⚠️ kept to record the source discrepancy.",
  },
  {
    key: "donepurple", zh: "done / 完成列紫（lane 语义）", en: "Done purple (lane semantics)",
    oldValue: ".purple · #bf5af2", oldSwatches: ["#bf5af2"],
    token: "--status-done（同 --purple 家族）", newDark: "#bf5af2", newLight: "#9440d6", swatchVars: ["--status-done"],
    sample: { kind: "chip", className: "chip", zh: "阶段性完成（lane 点见下）", en: "Done for now (lane dot below)" }, flagged: false,
  },
  {
    key: "delivered", zh: "已交付 / 验收于（delivered）", en: "Delivered / accepted",
    oldValue: ".green · #32d74b（完成列 accent）", oldSwatches: ["#32d74b"],
    token: "--success（.chip-success，tinted）", newDark: "#32d74b", newLight: "#218739", swatchVars: ["--success"],
    sample: { kind: "chip", className: "chip chip-success", zh: "验收于 2026-08-29", en: "accepted 2026-08-29" }, flagged: false,
  },
  {
    key: "needsinput", zh: "需输入 / 警告（needs input）", en: "Needs input / warning",
    oldValue: ".orange · #ff9f0a", oldSwatches: ["#ff9f0a"],
    token: "--warning（.chip-warning / --status-progress）", newDark: "#ff9f0a", newLight: "#c05d00", swatchVars: ["--warning"],
    sample: { kind: "chip", className: "chip chip-warning", zh: "需输入", en: "Input" }, flagged: false,
  },
  {
    key: "blocked", zh: "blocked / 恢复放弃（红受阻）", en: "Blocked / resume exhausted (red)",
    oldValue: ".red · #ff453a", oldSwatches: ["#ff453a"],
    token: "--danger（.chip-danger / .is-blocked 左边条用 --warning）", newDark: "#ff453a", newLight: "#d70015", swatchVars: ["--danger"],
    sample: { kind: "chip", className: "chip chip-danger", zh: "恢复已放弃", en: "Auto-resume exhausted" }, flagged: false,
  },
  {
    key: "waiting", zh: "waiting / capture 超时 notice（黄等待）", en: "Waiting / capture-timeout notice (yellow)",
    oldValue: ".yellow（macOS dark 解析 #ffd60a）", oldSwatches: ["#ffd60a"],
    token: "--notice（.chip-notice）", newDark: "#ffd60a", newLight: "#8a6d00", swatchVars: ["--notice"],
    sample: { kind: "chip", className: "chip chip-notice", zh: "等待：署名语言", en: "waiting: signature language" }, flagged: false,
    noteZh: "yellow 槽位已补（--notice）；light 按白底对比加深同族黄，调底用亮黄锚。",
    noteEn: "Yellow slot added (--notice); light darkens the same-hue yellow for contrast, tint uses the vivid anchor.",
  },
  {
    key: "deadline", zh: "截止紧急（days_left ≤ 3）", en: "Deadline urgent (≤ 3d)",
    oldValue: ".red · #ff453a（紧急截止文字标红）", oldSwatches: ["#ff453a"],
    token: "--danger（.chip-danger.chip-outline，红字描边档）", newDark: "#ff453a", newLight: "#d70015", swatchVars: ["--danger"],
    sample: { kind: "chip", className: "chip chip-danger chip-outline", zh: "2026-09-02（剩 2 天）", en: "2026-09-02 (2d left)" }, flagged: false,
    noteZh: "红回归：Mac 是红字，web 用阶梯第 3 档（outline+红字）同 hue 呈现（曾误走警告橙）。",
    noteEn: "Red restored: Mac renders red text; web uses the outline+text ladder step in the same hue (was wrongly orange).",
  },
  {
    key: "lineage-improves", zh: "lineage ↳ 改进（improvement_of）", en: "Lineage — improves",
    oldValue: ".teal · #6ac4dc（↳ 改进 #R-xx 行）", oldSwatches: ["#6ac4dc"],
    token: "--accent（详情抽屉 .zai-chip--improves，quiet tint）", newDark: "#6ac4dc", newLight: "#12758c", swatchVars: ["--accent"],
    sample: { kind: "chip", className: "zai-chip zai-chip--improves", zh: "↳ 改进自 R-88", en: "↳ Improves R-88" }, flagged: true,
    noteZh: "teal 已回归（quiet 档）；⚠️ 位置差异保留：Mac 在卡面行，web 在详情抽屉 chip。",
    noteEn: "Teal restored (quiet tier); ⚠️ placement still differs: Mac shows it on the card face, web in the detail drawer.",
  },
  {
    key: "lineage-repeated", zh: "被提×N / 回锅（re-raised）", en: "Raised ×N / returned",
    oldValue: ".orange · #ff9f0a（重复×N badge）", oldSwatches: ["#ff9f0a"],
    token: "--warning（.chip-warning.chip-quiet，lineage 安静档）", newDark: "#ff9f0a", newLight: "#c05d00", swatchVars: ["--warning"],
    sample: { kind: "chip", className: "chip chip-warning chip-quiet", zh: "被提×3", en: "Raised ×3" }, flagged: false,
    noteZh: "lineage 计数走 quiet tint（-soft-quiet），比状态 chip（需输入/受阻/等待）轻一档；回锅警示仍是重档。",
    noteEn: "Lineage counters use the quiet tint (-soft-quiet), one weight below state chips; the Returned alert stays heavy.",
  },
  {
    key: "lineage-merged", zh: "已并入（merged_into，紫被提及已并入）", en: "Merged into (purple)",
    oldValue: ".purple · #bf5af2（已并入×N badge，Cards.swift:1248）", oldSwatches: ["#bf5af2"],
    token: "--purple（详情抽屉 .zai-chip--merged，quiet tint）", newDark: "#bf5af2", newLight: "#9440d6", swatchVars: ["--purple"],
    sample: { kind: "chip", className: "zai-chip zai-chip--merged", zh: "已并入 R-42", en: "Merged into R-42" }, flagged: true,
    noteZh: "更正前版表记「无独立色」——Mac 卡面有紫 badge；web 现为抽屉 quiet 紫 chip；⚠️ 位置差异保留。",
    noteEn: "Corrects the earlier 'no dedicated color' claim — Mac has a purple card badge; web now shows a quiet purple drawer chip; ⚠️ records the placement difference.",
  },
  {
    key: "queued", zh: "queued / backlog 灰", en: "Queued / backlog gray",
    oldValue: ".gray · #98989d（systemGray 族）", oldSwatches: ["#98989d"],
    token: "--status-backlog / --status-todo + .is-queued 灰卡面", newDark: "#7c7c81 / #98989d", newLight: "#8e8e93", swatchVars: ["--status-backlog", "--status-todo"],
    sample: { kind: "chip", className: "chip", zh: "排队中", en: "Queued" }, flagged: false,
  },
  {
    key: "hard", zh: "硬需求（hardness=hard）", en: "Hard requirement",
    oldValue: ".red · #ff453a", oldSwatches: ["#ff453a"],
    token: "--danger（.chip-danger）", newDark: "#ff453a", newLight: "#d70015", swatchVars: ["--danger"],
    sample: { kind: "chip", className: "chip chip-danger", zh: "硬需求", en: "Hard" }, flagged: false,
  },
  {
    key: "accent", zh: "accent（会话活动 / 主按钮 / 焦点）", en: "Accent (session activity / primary / focus)",
    oldValue: ".teal · #6ac4dc", oldSwatches: ["#6ac4dc"],
    token: "--accent / -hover / -active / -soft / --on-accent", newDark: "#6ac4dc（hover #7ecfe4 / active #8ed2e4）", newLight: "#12758c（hover #10677b / active #0e596a）", swatchVars: ["--accent", "--accent-hover"],
    sample: { kind: "chip", className: "chip chip-accent", zh: "accent", en: "accent" }, flagged: false,
    noteZh: "hover/active 阶梯 token 化：light 逐级加深、dark 逐级提亮（hue 不变）。",
    noteEn: "Hover/active steps are tokens now: light darkens per step, dark lightens (hue unchanged).",
  },
  {
    key: "lanedots", zh: "列头色点（lane header dots）", en: "Lane header dots",
    oldValue: "老 app 列头无色点（web 新增视觉）", oldSwatches: [],
    token: "--status-todo / progress / review / done / backlog", newDark: "#98989d #ff9f0a #32d74b #bf5af2 #7c7c81", newLight: "#8e8e93 #c05d00 #218739 #9440d6 #8e8e93", swatchVars: [],
    sample: { kind: "dots" }, flagged: false,
    noteZh: "列语义→token 映射见 tokens.css 头注释（组件层约定）。",
    noteEn: "Lane → token mapping lives in the tokens.css header comment.",
  },
  {
    key: "bglayers", zh: "底色分层（窗口/侧栏/卡面/列头）", en: "Background layers",
    oldValue: "截图基准 #1b1d23 / #17191e / #23262e / #20232b", oldSwatches: ["#1b1d23", "#17191e", "#23262e", "#20232b"],
    token: "--bg / --sidebar-bg / --surface / --column-header", newDark: "同老值（继承）", newLight: "#fafbfc / #f1f3f6 / #ffffff / #f6f8fb", swatchVars: [],
    sample: { kind: "layers" }, flagged: false,
  },
];
