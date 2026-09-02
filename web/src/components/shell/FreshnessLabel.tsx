// 新鲜度标签（G7 shell，自写非 fork）：语义镜像 live 树 mac/Sources/Freshness.swift——
// generated_at 距今 >90s → 警告色「actd 可能未运行」；新鲜 → 次级色相对时间
// （刚刚/N分钟前/N小时前/N天前）；解析失败或无 board → 整个隐藏。
// Mac 版用 TimelineView 15s tick，这里等价用 setInterval(15s) 自驱重算。
import { useEffect, useState } from "react";
import { useI18n } from "../../i18n";
import { relativeAge } from "../../relativeTime";
import { useAppState } from "../../store";

const TICK_MS = 15_000;
const STALE_AFTER_SECONDS = 90; // 与 Freshness.swift 同阈值

/** ISO-8601（dashboard.json generated_at，如 2026-08-30T12:00:00Z）→ 毫秒时间戳；无效返回 null */
export function parseGeneratedAt(value: unknown): number | null {
  if (typeof value !== "string" || !value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

/** 相对时间档位（刚刚/N分钟前/N小时前/N天前）——单源在 relativeTime.ts，这里 re-export 保住既有 import 面 */
export { relativeAge } from "../../relativeTime";

export function FreshnessLabel() {
  const { text } = useI18n();
  const { board } = useAppState();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, []);

  const generatedAt = parseGeneratedAt(board?.generated_at);
  if (generatedAt == null) return null;

  const ageSeconds = Math.max(0, (now - generatedAt) / 1000);
  if (ageSeconds > STALE_AFTER_SECONDS) {
    const mins = Math.max(1, Math.floor(ageSeconds / 60));
    return (
      <span className="shell-freshness is-stale" role="status">
        {text(
          `数据生成于 ${mins} 分钟前，actd 可能未运行`,
          `Data generated ${mins} min ago — actd may be down`,
        )}
      </span>
    );
  }

  // 新鲜分支（≤90s）实际只会命中 刚刚/1分钟前——完整档位保留以镜像 Mac 版语义
  return (
    <span className="shell-freshness">
      {text("数据生成于 ", "Data generated ") + relativeAge(ageSeconds, text)}
    </span>
  );
}
