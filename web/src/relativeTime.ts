// 相对时间 / 时长文案——镜像原生 mac/Sources/Utils.swift RelativeTime.since 与
// Cards.swift 的 RelativeTime.sinceEpoch / duration（原生是冻结的行为规格，D3）：
//   sinceX：刚刚 / N分钟前 / N小时前 / N天前（EN just now / Nm ago / Nh ago / Nd ago）
//   duration：N秒 / N分钟 / N小时M分 / N天H小时（EN Ns / Nm / Nh Mm / Nd Hh）
// 卡面一律用相对时间，hover title 给绝对时间（absoluteLabel）。纯函数 + 一个
// 自驱 tick 的 useNow hook；不 import store。
import { useEffect, useState } from "react";

export type Text = (zh: string, en: string) => string;

/** 相对年龄（秒 → 文案）；FreshnessLabel / DeployLabel 也从这里取同一套档位 */
export function relativeAge(ageSeconds: number, text: Text): string {
  if (ageSeconds < 60) return text("刚刚", "just now");
  const mins = Math.floor(ageSeconds / 60);
  if (mins < 60) return text(`${mins}分钟前`, `${mins}m ago`);
  const hours = Math.floor(mins / 60);
  if (hours < 24) return text(`${hours}小时前`, `${hours}h ago`);
  return text(`${Math.floor(hours / 24)}天前`, `${Math.floor(hours / 24)}d ago`);
}

/** epoch 秒 → 相对年龄；非正数/非数字 → null（原生 sinceEpoch 的 guard e > 0） */
export function sinceEpoch(epoch: unknown, nowMs: number, text: Text): string | null {
  if (typeof epoch !== "number" || !Number.isFinite(epoch) || epoch <= 0) return null;
  return relativeAge(Math.max(0, nowMs / 1000 - epoch), text);
}

/** ISO-8601 字符串 → 相对年龄；空/不可解析 → null（原生 RelativeTime.since） */
export function sinceIso(iso: unknown, nowMs: number, text: Text): string | null {
  if (typeof iso !== "string" || !iso) return null;
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return null;
  return relativeAge(Math.max(0, (nowMs - parsed) / 1000), text);
}

/** 两个 epoch 秒之间的紧凑时长（原生 RelativeTime.duration；to < from 或缺失 → null） */
export function duration(from: unknown, to: unknown, text: Text): string | null {
  if (typeof from !== "number" || typeof to !== "number") return null;
  if (!Number.isFinite(from) || !Number.isFinite(to) || from <= 0 || to < from) return null;
  const secs = Math.floor(to - from);
  if (secs < 60) return text(`${secs}秒`, `${secs}s`);
  const mins = Math.floor(secs / 60);
  if (mins < 60) return text(`${mins}分钟`, `${mins}m`);
  const hours = Math.floor(mins / 60);
  if (hours < 24) {
    const m = mins % 60;
    return m === 0 ? text(`${hours}小时`, `${hours}h`) : text(`${hours}小时${m}分`, `${hours}h ${m}m`);
  }
  const days = Math.floor(hours / 24);
  const h = hours % 24;
  return h === 0 ? text(`${days}天`, `${days}d`) : text(`${days}天${h}小时`, `${days}d ${h}h`);
}

/** hover 用的绝对时间：epoch 秒或 ISO 字符串 → 本地化完整时间；解析不了 → undefined */
export function absoluteLabel(value: unknown, locale: string): string | undefined {
  let ms: number | null = null;
  if (typeof value === "number" && Number.isFinite(value) && value > 0) ms = value * 1000;
  else if (typeof value === "string" && value) {
    const parsed = Date.parse(value);
    ms = Number.isNaN(parsed) ? null : parsed;
  }
  return ms == null ? undefined : new Date(ms).toLocaleString(locale);
}

/** 自驱重算的「现在」（默认 30s tick——卡面相对时间最细档是分钟） */
export function useNow(tickMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), tickMs);
    return () => clearInterval(timer);
  }, [tickMs]);
  return now;
}
