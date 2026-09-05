// 看板动作装配（G2）：payload 按 live CONTRACT §3/§10 + act/webui.py `_INBOX_KEYS` 逐字段构造。
// 纪律（server zero-tolerance，多一个字段 400 UNKNOWN_FIELD）：
//   - **不上送 `ts`**——webui 契约「ts 一律 server 端(重)盖章，客户端不可伪造」，
//     `ts` 不在 _INBOX_KEYS 白名单里，带上就是 400；
//   - 卡片动词恒带显式 `comment`（null 或文本），镜像 Mac writeInbox 四键形；
//   - 无乐观更新：动作发出 → SSE board.updated → refreshBoard 回流（CONVENTIONS §4），
//     解锁看的是回流里这条动作的**真信号**（pendingSettle.ts，§39.3 / §21bis），不是 generated_at。
import { useEffect, useRef, useState } from "react";
import { ApiError, postAction } from "../../api";
import { useI18n } from "../../i18n";
import { buildAppUrl, readPage } from "../../route";
import { steerAcknowledged } from "../../steer";
import { getState, selectCard, setArchiveStripExpanded, setBacklogStripExpanded, useAppState } from "../../store";
import { LANE_VERBS, landed, recordPending, timeoutNotice, type PendingRecord } from "./pendingSettle";

/** 卡片决策类四键形（comment 键永远存在，无文本时 null——inbox-actions.md §2） */
export function cardAction(id: string, action: string, comment: string | null = null) {
  return { action, comment, id };
}

/** 打回空反馈的固定自查指令——Mac 客户端字面量（inbox-actions.md §2.10，逐字复刻，勿改一字） */
export const REWORK_EMPTY_FALLBACK =
  "Zelin 打回了这次交付但没有写具体理由。请对照本需求的 definition_of_done 逐条自检：" +
  "每一条是否真正达成、产出物是否在承诺的位置、质量是否达到可直接使用的程度。" +
  "找出差距，自行改进后重新交付，并用两三句话说明这次改了什么。";

export function clipCodePoints(s: string, max: number): string {
  const points = [...s];
  return points.length <= max ? s : points.slice(0, max).join("");
}

/** W17（§50）：审批闸门读生效档位——投影 effective_tier 缺席（旧 server）才回落声明 tier */
export function effectiveTier(card: { tier: string; effective_tier?: string }): string {
  return typeof card.effective_tier === "string" && card.effective_tier ? card.effective_tier : card.tier;
}

/** T2 确认弹窗的金额行——镜像 AppDelegate.confirmT2 的推导（cost_state=="unknown" 视为未知） */
export function costLine(
  card: Record<string, unknown>,
  text: (zh: string, en: string) => string,
): string {
  const money = moneyOf(card);
  if (money) return text(`预计费用：${money}`, `Estimated cost: ${money}`);
  return text("成本未知", "Cost unknown");
}

/** 金额字串（原生 Self.money：整数不带小数）；成本未知 / 无数字 → null */
export function moneyOf(card: Record<string, unknown>): string | null {
  const cost = card["cost_usd"];
  if (typeof cost === "number" && card["cost_state"] !== "unknown") {
    return Number.isInteger(cost) ? `$${cost}` : `$${cost.toFixed(2)}`;
  }
  return null;
}

/** 详情侧栏里提案的金额行（原生 ApprovalCardView.costText：「💰 预计费用: $N」/「💰 成本未知」，ASCII 冒号） */
export function costText(card: Record<string, unknown>, text: (zh: string, en: string) => string): string {
  const money = moneyOf(card);
  return money ? text(`💰 预计费用: ${money}`, `💰 Estimated cost: ${money}`) : text("💰 成本未知", "💰 Cost unknown");
}

/** 状态正确的会话命令（原生 TaskRow.cmd）：copy_cmd 优先，其次 claude --resume <sid>；排队卡无。
 *  卡面「单击复制指令」行与详情侧栏「指令：」行同一来源（投影行与 /api/cards 详情都带这几个键） */
export function resumeCommand(row: Record<string, unknown>): string | null {
  if (row.state === "queued") return null;
  if (typeof row.copy_cmd === "string" && row.copy_cmd) return row.copy_cmd;
  if (typeof row.session_id === "string" && row.session_id) return `claude --resume ${row.session_id}`;
  return null;
}

/** tier 章的大白话（原生 tierLine 的词表；管线的 tier_hint 只有中文且与本表 zh 逐字相同，
 *  所以按 tier 取本地双语表；未知 / 缺席 tier 读 未分级——原生同样「never T?」） */
export function tierHint(card: Record<string, unknown>, text: (zh: string, en: string) => string): string {
  switch (card["tier"]) {
    case "T0": return text("自动执行", "runs automatically");
    case "T1": return text("一键可批", "one-click approve");
    case "T2": return text("需文字确认", "needs written confirmation");
  }
  return text("未分级", "Untiered");
}

/** 截止倒数短语（原生 deadline phrase）：已逾期 N 天 / 今天截止 / 还剩 N 天；days_left 缺席 → null */
export function deadlinePhrase(daysLeft: unknown, text: (zh: string, en: string) => string): string | null {
  if (typeof daysLeft !== "number" || !Number.isFinite(daysLeft)) return null;
  if (daysLeft < 0) return text(`已逾期 ${-daysLeft} 天`, `${-daysLeft} d overdue`);
  if (daysLeft === 0) return text("今天截止", "due today");
  return text(`还剩 ${daysLeft} 天`, `${daysLeft} d left`);
}

/** 难度章（原生 hardnessLabel：hard → 较难 / soft → 常规；其它原样） */
export function hardnessLabel(value: unknown, text: (zh: string, en: string) => string): string | null {
  if (value === "hard") return text("较难", "Hard");
  if (value === "soft") return text("常规", "Routine");
  return typeof value === "string" && value ? value : null;
}

/** 打开/关闭详情抽屉 + 同步 ?card= 深链（CONVENTIONS：深链只经 route.ts） */
export function openCardDetail(cardId: string | null) {
  selectCard(cardId);
  const url = buildAppUrl(window.location.href, readPage(window.location.search), cardId);
  window.history.replaceState(null, "", url);
}

export interface SubmitState {
  /** 已提交、等看板回流（按钮行整体禁用，杜绝双击重复提交——§41 iOS busy 模式的 web 等价） */
  pending: boolean;
  error: string | null;
  /** 最近一次提交被 server 标注为 steer（executing 卡上的 comment，响应键 steer:true）——
   *  pending 期间的「方向修正排队中」回执 chip 用；看板回流后以投影 steers[] 为准 */
  steerQueued: boolean;
  /** in-flight 的动作词（body.action）：卡面的等待句按它选原生文案（pendingNote） */
  pendingAction: string | null;
  submit: (body: Record<string, unknown>) => Promise<boolean>;
  clearError: () => void;
}

/** Mac Store.swift 同款 180s truth-timeout：真信号迟迟不来 → 解锁 + 诚实报未确认 */
export const CONFIRM_TIMEOUT_MS = 180_000;

/** v0.33 书立条强制展开（§54.1 追记）：用户点了按钮，回执不能落在收起的条里（原生 Store.swift）。
 *  - 提交成功（`submitted`，原生 applyAction 在 inbox 写成功后跑）：暂缓 = echo 落潜在任务条（addEcho target .debt，:861）
 *    → 左条；放回看板 = info 条落永久性完成条（beginReturn source .archived，:851）→ 右条。永久完成（archive）的 echo
 *    不开右条——原生只对 target .debt 开左条，右条只因 unarchive 打开。
 *  - 180 s 超时（`timeout`，原生 sweepTimeouts）：从潜在任务条发出的**换列动词**（研究并提议 / 删除 / 永久完成）超时通知落回该条、
 *    卡也在那里静默恢复（:425 raise、:450 `e.source == .debt`）→ 左条；放回看板超时（:539 `entry.source == .archived`）→ 右条；
 *    暂缓超时卡还在提案列，不开。只认 raise / echo / return 三族（= LANE_VERBS）：详情抽屉里对 debt / archived 卡的改名
 *    （set_title）、拆卡（split_note）、修改意见（comment）超时——原生 expiredTitles / expiredSplits / expiredComments
 *    （:452-473 / :516-526）不碰任何条，这里同样不开。
 *  注意超时半边只在发出动作的卡组件仍挂着时生效：两条书立条收起即卸载条内的卡（`{expanded && …}`），useSubmit 的
 *  兜底定时器随组件卸载丢弃（#253 的 pending 状态是组件级的，不是原生 raisingLocal / pendingEchoes 那样的 store 级台账）。 */
export function stripToForceOpen(
  rec: Pick<PendingRecord, "action" | "sourceLane">,
  phase: "submitted" | "timeout",
): "backlog" | "archive" | null {
  if (phase === "submitted") {
    if (rec.action === "defer") return "backlog";
    if (rec.action === "unarchive") return "archive";
    return null;
  }
  if (!LANE_VERBS.has(rec.action ?? "")) return null;
  if (rec.sourceLane === "debt") return "backlog";
  if (rec.sourceLane === "archived") return "archive";
  return null;
}

function forceOpenStrip(rec: Pick<PendingRecord, "action" | "sourceLane">, phase: "submitted" | "timeout") {
  const strip = stripToForceOpen(rec, phase);
  if (strip === "backlog") setBacklogStripExpanded(true);
  else if (strip === "archive") setArchiveStripExpanded(true);
}

/**
 * 每张卡一个提交状态机：submit → pending=true；失败即解锁并给出可读错误；
 * 成功后保持「已提交…」直到这条动作在看板快照里**真的落地**才解锁（pendingSettle.landed：
 * 换列动词 = id 离开原列、comment = plan 变 / steers 增、set_title = 后台名等于新名、
 * merge_force = 副卡全消失……原生 PendingSweep.cleared(by:) 逐动词判据）——不是 generated_at
 * 一变就解锁：actd 每个 pass 结尾都重写看板，与这张卡动没动无关（§39.3「generated_at bump
 * 不清（§21bis 先例）」）。没有乐观更新，回流里的真信号是唯一的成功回执。180s 没等到 →
 * 解锁并按动词给诚实文案（pendingSettle.timeoutNotice，镜像 Store.swift sweepTimeouts）。
 */
export function useSubmit(): SubmitState {
  const { board } = useAppState();
  const { text } = useI18n();
  const [pending, setPending] = useState(false);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [steerQueued, setSteerQueued] = useState(false);
  const record = useRef<PendingRecord | null>(null);

  useEffect(() => {
    // 每一版快照（哪怕 generated_at 没变——同版重拉）都跑一遍谓词；提交那一刻也跑（同名改名之类
    // 一出生就满足的记录不该白等 180 s——原生「闸门跳过路径共用一份谓词」的教训）
    const rec = record.current;
    if (!pending || !rec || !board) return;
    if (landed(rec, board)) {
      setPending(false);
      setSteerQueued(false); // 回流后 steer 状态以投影 steers[] 为准，本地回执退场
      record.current = null;
    }
  }, [board, pending]);

  useEffect(() => {
    if (!pending) return undefined;
    const timer = window.setTimeout(() => {
      const rec = record.current;
      record.current = null;
      setPending(false);
      setError(timeoutNotice(rec, getState().board, text));
      // 超时通知落回发出动作的那条书立条 → 那条不能是收起的（原生 :425 / :450 / :539）
      if (rec) forceOpenStrip(rec, "timeout");
    }, CONFIRM_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [pending, text]);

  const submit = async (body: Record<string, unknown>): Promise<boolean> => {
    const rec = recordPending(body, getState().board);
    record.current = rec;
    setPending(true);
    setPendingAction(typeof body.action === "string" ? body.action : null);
    setError(null);
    setSteerQueued(false);
    try {
      const response = await postAction(body);
      setSteerQueued(steerAcknowledged(response));
      // 原生 applyAction 的时点（inbox 写成功后）：暂缓 → 开潜在任务条；放回看板 → 开永久性完成条。放在这里而不是
      // landed 路径：换列动词落地的那一帧卡组件已随卡离开原列卸载，落地 effect 不会跑
      forceOpenStrip(rec, "submitted");
      return true;
    } catch (e) {
      setPending(false);
      record.current = null;
      setError(describeActionError(e, text));
      return false;
    }
  };

  return { pending, pendingAction, error, steerQueued, submit, clearError: () => setError(null) };
}

/** 等看板回流期间卡面的一句（原生 Store 的乐观 notice 文案，Store.swift:708–792 / Cards.swift:1117）：
 *  不是乐观更新——卡不换列、按钮只是禁用；回流后整句退场。未知动作词退到「已提交…」。 */
export function pendingNote(action: string | null, text: (zh: string, en: string) => string): string {
  switch (action) {
    case "approve": return text("启动中…", "Starting…");
    case "rework": return text("打回处理中…", "Sending back…");
    case "accept": return text("验收确认中…", "Accepting…");
    case "defer": return text("暂缓中…", "Moving to backlog…");
    case "abort_execution": return text("停止中，卡片将回到提案列", "Stopping — card returns to Proposals");
    case "stop_to_review": return text("停止中，卡片将去待验收", "Stopping — card moves to Review");
    case "revert_review": return text("退回中，卡片将回到待验收", "Reverting to review");
    case "done_external": return text("已办完", "done outside");
    case "comment": return text("修改意见合并中…", "Merging your feedback…");
    case "feedback": return text("已记录建议，感谢", "Feedback recorded");
    default: return text("已提交…", "Submitted…");
  }
}

/** 动作失败的用户可读文案；501 = G1 inbox_writer 尚未接线（F1 约定的过渡语义） */
export function describeActionError(
  e: unknown,
  text: (zh: string, en: string) => string,
): string {
  if (e instanceof ApiError && e.status === 501) {
    return text("动作通道尚未接线（服务端 501）", "Action channel not wired yet (server 501)");
  }
  return e instanceof Error ? e.message : String(e);
}
