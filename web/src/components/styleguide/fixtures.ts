// 活体样式指南的 fixture 数据（?page=styleguide 专用，不进任何 wire）。
// 纪律：形状严格照 types.ts 的投影镜像造——指南渲染的是【真组件】，fixture 只是喂 props；
// id 用 SG- 前缀避免与真卡撞号（点按钮会真发 POST /api/actions，server 端 NOT_FOUND 拒绝，无副作用）。
import type { ApprovalCard, DebtCard, ReviewCard, TaskRow } from "../../types";

/** 提案 T1：常见徽章齐全（deadline 紧急 / 成本 / 被提×N / green_sign / 分歧 / 回锅） */
export const PROPOSAL_T1: ApprovalCard = {
  id: "SG-T1",
  title: "Styleguide fixture — T1 proposal with the full badge row",
  summary: "整理本周会议纪要并起草跟进邮件（fixture：徽章全开的 T1 提案卡）。",
  tier: "T1",
  tier_hint: "需要批准",
  deadline: "2026-09-02",
  days_left: 2,
  repeated: 3,
  cost_usd: 2,
  show_cost: true,
  green_sign: true,
  disagreement: "两条来源对交付格式说法不一致。",
  reraised: true,
  reraised_note: "上轮暂缓后信号再次出现，重新出卡。",
  silent_merged: 2,
  target_kind: "existing",
  target_repo: "/Users/zelin/Projects/your-workbench",
  processing: false,
  delivery_mode: "chat",
  sources: [{ who: "Zelin", channel: "manual", date: "2026-08-29", quote: "styleguide fixture" }],
  plan: ["收集来源", "起草成稿"],
  dod: ["成稿可直接发送"],
};

/** 提案 T2：typed-confirm 档 + 硬需求 chip */
export const PROPOSAL_T2: ApprovalCard = {
  id: "SG-T2",
  title: "Styleguide fixture — T2 proposal",
  summary: "改动生产配置并重启守护进程（fixture：T2 批准需键入确认）。",
  tier: "T2",
  tier_hint: "键入确认",
  hardness: "hard",
  cost_usd: 12,
  show_cost: true,
  target_kind: "existing",
  target_name: "zelin-ai-assistant",
  processing: false,
  sources: [],
  plan: ["备份现网配置", "灰度重启"],
  dod: ["服务健康检查通过"],
};

/** raising 占位灰卡（processing=true：只有 sheen，无决策按钮） */
export const PROPOSAL_PROCESSING: ApprovalCard = {
  id: "SG-RAISING",
  title: "AI 正在研究的占位卡（fixture）",
  tier: "T1",
  show_cost: false,
  processing: true,
  sources: [],
  plan: [],
  dod: [],
};

/** queued 灰卡：结构化排队原因 + dispatch_error chip */
export const TASK_QUEUED: TaskRow = {
  id: "SG-QUEUED",
  name: "排队中的已批准任务（fixture）",
  state: "queued",
  summary: "等前置卡完成后自动派发。",
  queued_reason: { kind: "waiting_card", blocking_id: "R-101" },
  dispatch_error: "上次派发失败：spawn timeout",
};

/** working 卡：sheen 行 + steer 三态回执 chips + last_error */
export const TASK_WORKING: TaskRow = {
  id: "SG-WORKING",
  name: "执行中的任务（fixture）",
  state: "working",
  agent_name: "调研代号 Falcon",
  summary: "后台会话正在跑，卡面显示方向修正回执。",
  cwd: "/Users/zelin/Projects/acme-site",
  copy_cmd: "claude attach falcon",
  started_at: Math.floor(Date.now() / 1000) - 2 * 3600 - 59 * 60,
  last_error: "上一轮重试自动恢复成功（示例告警行）。",
  steers: [
    { ts: "2026-08-30T10:00:00Z", text: "改用中文写", status: "queued" },
    { ts: "2026-08-30T09:00:00Z", text: "范围缩小到本周", status: "delivered" },
    { ts: "2026-08-30T08:00:00Z", text: "换个标题", status: "dropped" },
  ],
};

/** needs_input blocked 卡：问题正文 + 恢复放弃 + waiting_for */
export const TASK_BLOCKED: TaskRow = {
  id: "SG-BLOCKED",
  name: "等你回答的任务（fixture）",
  state: "blocked",
  question: "邮件署名用中文还是英文？",
  waiting_for: "署名语言",
  resume_exhausted: true,
};

/** 待验收卡：交付摘要 + DoD 清单 + final_draft（复制成稿按钮出现） */
export const REVIEW_FIXTURE: ReviewCard = {
  id: "SG-REVIEW",
  name: "待验收的交付（fixture）",
  delivered_summary: "已按 DoD 完成成稿，正文见 final_draft。",
  final_draft: "（fixture 成稿正文——复制按钮拷贝的就是这段。）",
  dod: ["覆盖三条来源", "语气与旧稿一致"],
  delivery_mode: "chat",
  cwd: "/Users/zelin/Projects/your-workbench",
  copy_cmd: "cd '/Users/zelin/Projects/your-workbench' && claude --resume 0000-fixture",
  dispatched_at: Math.floor(Date.now() / 1000) - 5 * 3600,
  review_at: Math.floor(Date.now() / 1000) - 2 * 3600 - 10 * 60,
};

/** 阶段性完成卡：验收于 chip + 两动词 */
export const TASK_DONE: TaskRow = {
  id: "SG-DONE",
  name: "阶段性完成的任务（fixture）",
  state: "delivered",
  delivered_summary: "交付已验收，等对方反馈。",
  cwd: "/Users/zelin/Projects/acme-site",
  copy_cmd: "claude --resume 1111-fixture",
  accepted_at: Math.floor(Date.now() / 1000) - 19 * 86400,
};

/** 潜在任务（debt）卡：type + 硬需求 chips + 两动词 */
export const DEBT_FIXTURE: DebtCard = {
  id: "SG-DEBT",
  title: "潜在任务（fixture）：调研竞品定价页",
  type: "research",
  hardness: "hard",
  summary: "还没到要做的程度，先挂着。",
};
