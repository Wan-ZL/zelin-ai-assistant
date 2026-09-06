// §21bis 强制合并 180 s 没落地的诚实超时条（原生 Store.swift sweepTimeouts 的 notice-merge-force：lane .approval，
// kind .raiseTimeout——橙色，一批过期只出一条）。数据 = store.forceMergeTimedOutAt（章退场的同一笔落时间戳）；
// 点 × 或 120 s（原生 notices 自然褪去的时长）后归 null。提案列 composer 之下、并入回执之旁。
// 120 s 从**落时间戳**起算，不从本组件挂载起算（原生 sweep 按 notice.created 清，与哪个视图在前无关）：超时在
// 设置页期间发生、十分钟后回看板，条已过期 → 直接归 null，不再复活一整段 120 s。
import { useEffect } from "react";
import { useI18n } from "../../i18n";
import { dismissForceMergeTimeout, useAppState } from "../../store";

/** 原生 LocalNotice 120 s 自动褪去 */
export const NOTICE_FADE_MS = 120_000;

/** 距自动褪去还剩多少 ms（≤0 = 已过期） */
function remainingFade(timedOutAt: number): number {
  return NOTICE_FADE_MS - (Date.now() - timedOutAt);
}

export function ForceMergeTimeoutNotice() {
  const { text } = useI18n();
  const { forceMergeTimedOutAt } = useAppState();

  useEffect(() => {
    if (forceMergeTimedOutAt === null) return undefined;
    const remaining = remainingFade(forceMergeTimedOutAt);
    if (remaining <= 0) {
      dismissForceMergeTimeout();
      return undefined;
    }
    const timer = window.setTimeout(dismissForceMergeTimeout, remaining);
    return () => window.clearTimeout(timer);
  }, [forceMergeTimedOutAt]);

  if (forceMergeTimedOutAt === null || remainingFade(forceMergeTimedOutAt) <= 0) return null;
  return (
    <p className="fold-receipt is-timeout" role="status" data-notice="merge-force-timeout">
      <span className="fold-receipt-text">
        {text("强制合并未确认，卡片未变化，请重试（检查 actd 是否在运行）", "Force-merge never confirmed — nothing changed, try again (check that actd is running)")}
      </span>
      <button type="button" className="fold-receipt-dismiss" aria-label={text("知道了", "Got it")} title={text("知道了", "Got it")} onClick={dismissForceMergeTimeout}>×</button>
    </p>
  );
}
