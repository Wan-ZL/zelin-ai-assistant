// 新鲜度标签（G7 shell，自写非 fork）：语义镜像 live 树 mac/Sources/Freshness.swift——
// generated_at 距今 >90s → 警告色「actd 可能未运行」；新鲜 → 次级色相对时间
// （刚刚/N分钟前/N小时前/N天前）；解析失败或无 board → 整个隐藏。
// Mac 版用 TimelineView 15s tick，这里等价用 setInterval(15s) 自驱重算。
// 计算住 useFreshness（HeaderBar 调一次：full / compact 渲染成标签，tight 折进连接点的 tooltip，
// §49 追记 2026-09-04）；FreshnessLabel 只管渲染。
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

export interface Freshness {
  /** 「数据生成于 」——原生 Freshness.swift 两个 Text 的前一个（探针按节点判） */
  prefix: string;
  /** 相对时间；陈旧时带「actd 可能未运行」 */
  age: string;
  stale: boolean;
}

/** 整句（tooltip 用）：前缀 + 相对时间 */
export function freshnessText(value: Freshness): string {
  return value.prefix + value.age;
}

export function useFreshness(): Freshness | null {
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
  const prefix = text("数据生成于 ", "Data generated ");
  if (ageSeconds > STALE_AFTER_SECONDS) {
    const mins = Math.max(1, Math.floor(ageSeconds / 60));
    return { prefix, age: text(`${mins} 分钟前，actd 可能未运行`, `${mins} min ago — actd may be down`), stale: true };
  }
  // 新鲜分支（≤90s）实际只会命中 刚刚/1分钟前——完整档位保留以镜像 Mac 版语义
  return { prefix, age: relativeAge(ageSeconds, text), stale: false };
}

export function FreshnessLabel({ value }: { value: Freshness | null }) {
  if (!value) return null;
  // 前缀与相对时间各一个节点（原生 Freshness.swift 两个 Text；探针按节点判「数据生成于 」）
  return (
    <span className={`shell-freshness${value.stale ? " is-stale" : ""}`} role={value.stale ? "status" : undefined}>
      <span>{value.prefix}</span>
      <span>{value.age}</span>
    </span>
  );
}
