// capture 回执的寿命（CONTRACT §10 / §41 2026-09-05 追记；原生 Store.swift:343-353 sweepTimeouts · :652-659 updateHealth
// 重新起算 · PendingSweep.swift:169-192 captureMatches · Store.beginCapture）——列顶输入框（LaneComposer）与提案列头
// 「清理积压」（ProposalsTriageButton）共用这一份：两者在原生都是 `store.beginCapture(text, run:)` 同一张占位卡，
// 寿命规矩自然也只有一套。纯函数半边在 captureReceipt.ts；这里只管 React 状态与三个时钟：
//   - 对账：只看提交**之后**到的快照（generated_at 变了），带来一行属于这次提交的卡即清（captureLanded）；
//   - 超时：300 s（propose）/ 180 s（run）后换成诚实超时条；管线不 ok 时不计时，恢复时重新起算整段窗口；
//   - 褪去：超时条 120 s 后消失。
// 只有下一次**成功的**捕获才替换回执（原生 writeInboxFile 失败不 beginCapture、斜杠命令不进 store——旧占位卡照旧
// 活着、时钟照旧走）；失败句 / 提示行 / 斜杠回执要暂时顶掉它是调用方渲染栈的事，不动这里的状态。
import { useCallback, useEffect, useState } from "react";
import { getState, useAppState } from "../../store";
import {
  CAPTURE_NOTICE_FADE_MS,
  CAPTURE_TIMEOUT_MS,
  captureLanded,
  captureStem,
  pipelineStalled,
  type CaptureIdentity,
  type CaptureMode,
} from "./captureReceipt";

/** 上一次成功捕获的回执：对账凭据（原话 + inbox stem）+ 提交时看板的 generated_at（只对**之后**到的快照做对账）
 *  + 是否已超时成通知条 */
export interface CaptureReceipt extends CaptureIdentity {
  generatedAt: string | null;
  timedOut: boolean;
}

export interface CaptureReceiptState {
  receipt: CaptureReceipt | null;
  /** 原生 P1-4 `pipelineHealth != .ok`：回执改口、超时不计时 */
  stalled: boolean;
  /** POST /api/actions 成功后调用：原话 + server 响应（取 `file` stem 作精确键，§49）——替换上一份回执、时钟重来 */
  begin: (text: string, response: unknown) => void;
}

export function useCaptureReceipt(mode: CaptureMode): CaptureReceiptState {
  const { health, board } = useAppState();
  const stalled = pipelineStalled(health);
  const [receipt, setReceipt] = useState<CaptureReceipt | null>(null);

  // 回执对账（原生 PendingSweep.captureMatches）：提交那一刻的看板里若已有同词卡，回执不该在同一帧消失（用户得先看见
  // 「已提交」）；下一版快照里它还在（merge_or_new 并入了它）才算落地
  useEffect(() => {
    if (!receipt || receipt.timedOut || !board || board.generated_at === receipt.generatedAt) return;
    if (captureLanded(receipt, mode, board)) setReceipt(null);
  }, [board, receipt, mode]);

  // 超时（原生 sweepTimeouts）：管线不 ok 时不计时——占位句已诚实说「已保存到队列」，此时报超时是假警报；
  // 恢复 ok 时 effect 重跑 = 重新起算整段窗口（原生 updateHealth 重置 created）
  useEffect(() => {
    if (!receipt || receipt.timedOut || stalled) return undefined;
    const timer = window.setTimeout(
      () => setReceipt((r) => (r === receipt ? { ...r, timedOut: true } : r)),
      CAPTURE_TIMEOUT_MS[mode],
    );
    return () => window.clearTimeout(timer);
  }, [receipt, stalled, mode]);

  // 超时条 120 s 自然褪去（原生 notices 的寿命）
  useEffect(() => {
    if (!receipt?.timedOut) return undefined;
    const timer = window.setTimeout(() => setReceipt((r) => (r === receipt ? null : r)), CAPTURE_NOTICE_FADE_MS);
    return () => window.clearTimeout(timer);
  }, [receipt]);

  // generated_at 现读 store（POST 期间看板可能已刷新）：只认比它新的快照
  const begin = useCallback((text: string, response: unknown) => {
    setReceipt({ text, stem: captureStem(response), generatedAt: getState().board?.generated_at ?? null, timedOut: false });
  }, []);

  return { receipt, stalled, begin };
}
