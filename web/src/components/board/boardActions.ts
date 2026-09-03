// 看板动作装配（G2）：payload 按 live CONTRACT §3/§10 + act/webui.py `_INBOX_KEYS` 逐字段构造。
// 纪律（server zero-tolerance，多一个字段 400 UNKNOWN_FIELD）：
//   - **不上送 `ts`**——webui 契约「ts 一律 server 端(重)盖章，客户端不可伪造」，
//     `ts` 不在 _INBOX_KEYS 白名单里，带上就是 400；
//   - 卡片动词恒带显式 `comment`（null 或文本），镜像 Mac writeInbox 四键形；
//   - 无乐观更新：动作发出 → SSE board.updated → refreshBoard 回流（CONVENTIONS §4）。
import { useEffect, useRef, useState } from "react";
import { ApiError, postAction } from "../../api";
import { useI18n } from "../../i18n";
import { buildAppUrl, readPage } from "../../route";
import { steerAcknowledged } from "../../steer";
import { selectCard, useAppState } from "../../store";

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

/** 展开详情里的金额行（原生 ApprovalCardView.costText：「💰 预计费用: $N」/「💰 成本未知」，ASCII 冒号） */
export function costText(card: Record<string, unknown>, text: (zh: string, en: string) => string): string {
  const money = moneyOf(card);
  return money ? text(`💰 预计费用: ${money}`, `💰 Estimated cost: ${money}`) : text("💰 成本未知", "💰 Cost unknown");
}

/** tier 章的大白话（原生 tierLine：管线 hint 缺席时按 tier 兜底；未知 tier 读 未分级） */
export function tierHint(card: Record<string, unknown>, text: (zh: string, en: string) => string): string {
  const hint = card["tier_hint"];
  if (typeof hint === "string" && hint) return hint;
  switch (card["tier"]) {
    case "T0": return text("自动执行", "runs automatically");
    case "T1": return text("一键可批", "one-click approve");
    case "T2": return text("需文字确认", "needs written confirmation");
    default: return text("未分级", "Untiered");
  }
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

/** Mac Store.swift 同款 180s truth-timeout：回流迟迟不来 → 解锁 + 诚实报未确认 */
export const CONFIRM_TIMEOUT_MS = 180_000;

/**
 * 每张卡一个提交状态机：submit → pending=true；失败即解锁并给出可读错误；
 * 成功后保持「已提交…」直到看板 generated_at 变化（SSE 回流落地）才解锁——
 * 没有乐观更新，回流就是唯一的成功回执。180s 无回流 → 解锁并报「backend
 * 未确认」（镜像 Mac 端 180s fallback，绝不永远挂在「已提交…」上装成功）。
 */
export function useSubmit(): SubmitState {
  const { board } = useAppState();
  const { text } = useI18n();
  const generatedAt = board?.generated_at ?? null;
  const [pending, setPending] = useState(false);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [steerQueued, setSteerQueued] = useState(false);
  const sentAt = useRef<string | null>(null);

  useEffect(() => {
    if (pending && generatedAt !== sentAt.current) {
      setPending(false);
      setSteerQueued(false); // 回流后 steer 状态以投影 steers[] 为准，本地回执退场
      sentAt.current = null;
    }
  }, [generatedAt, pending]);

  useEffect(() => {
    if (!pending) return undefined;
    const timer = window.setTimeout(() => {
      setPending(false);
      sentAt.current = null;
      setError(text(
        "已提交，但 180 秒内看板未回流——backend 未确认，请检查 actd 是否在运行。",
        "Submitted, but the board never refreshed within 180s — backend unconfirmed; check that actd is running.",
      ));
    }, CONFIRM_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [pending, text]);

  const submit = async (body: Record<string, unknown>): Promise<boolean> => {
    setPending(true);
    setPendingAction(typeof body.action === "string" ? body.action : null);
    setError(null);
    setSteerQueued(false);
    sentAt.current = generatedAt;
    try {
      const response = await postAction(body);
      setSteerQueued(steerAcknowledged(response));
      return true;
    } catch (e) {
      setPending(false);
      sentAt.current = null;
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
    case "abort_execution": case "stop_to_review": return text("停止中，卡片将去待验收", "Stopping — card moves to Review");
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
