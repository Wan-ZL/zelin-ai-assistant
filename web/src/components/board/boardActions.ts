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

/** §39.2 answer_input 上限（code points；JS 展开即 code points，与 actd 复验同单位） */
export const ANSWER_MAX_CODE_POINTS = 4000;

export function clipCodePoints(s: string, max: number): string {
  const points = [...s];
  return points.length <= max ? s : points.slice(0, max).join("");
}

/** T2 确认弹窗的金额行——镜像 AppDelegate.confirmT2 的推导（cost_state=="unknown" 视为未知） */
export function costLine(
  card: Record<string, unknown>,
  text: (zh: string, en: string) => string,
): string {
  const cost = card["cost_usd"];
  if (typeof cost === "number" && card["cost_state"] !== "unknown") {
    const money = Number.isInteger(cost) ? `$${cost}` : `$${cost.toFixed(2)}`;
    return text(`预计费用：${money}`, `Estimated cost: ${money}`);
  }
  return text("成本未知", "Cost unknown");
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
  submit: (body: Record<string, unknown>) => Promise<boolean>;
  clearError: () => void;
}

/**
 * 每张卡一个提交状态机：submit → pending=true；失败即解锁并给出可读错误；
 * 成功后保持「已提交…」直到看板 generated_at 变化（SSE 回流落地）才解锁——
 * 没有乐观更新，回流就是唯一的成功回执。
 */
export function useSubmit(): SubmitState {
  const { board } = useAppState();
  const { text } = useI18n();
  const generatedAt = board?.generated_at ?? null;
  const [pending, setPending] = useState(false);
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

  const submit = async (body: Record<string, unknown>): Promise<boolean> => {
    setPending(true);
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

  return { pending, error, steerQueued, submit, clearError: () => setError(null) };
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
