// 顶栏密度档（CONTRACT §49 追记 2026-09-04；owner「在窄窗口中顶部会挤成多行」）：顶栏永远一行，
// 内容按实测宽度分三档——full（今天的行内布局）/ compact（chips + 排序收进「筛选」popover）/
// tight（搜索折叠成图标、壳开关只留图标、左侧只剩标题）。判档看 .shell-header 的实测宽
// （ResizeObserver），**不看视口**——顶栏宽 = 视口 − 导航栏，导航栏折叠 / 拖宽都要跟。
// 阈值单源 = tokens.css 的 --header-density-*（挂载时 getComputedStyle 读一次，随 --text-scale
// 等比放大）；壳里多两颗带字的开关、英文文案更长——两个静态加项（--header-density-shell-extra /
// --header-density-en-extra）也从 token 读，且只取决于桥在不在 / 当前语言，不取决于档位本身，
// 所以不会「收起 → 空间够了 → 展开 → 又不够」地来回抖。jsdom 没有 ResizeObserver（或读不到
// token）→ 恒为 full：判卷面与既有判例看到的还是今天的顶栏。
import { createContext, useContext, useEffect, useState, type RefObject } from "react";

export type HeaderDensity = "full" | "compact" | "tight";

export const HEADER_DENSITIES: readonly HeaderDensity[] = ["full", "compact", "tight"];

export interface DensityThresholds {
  /** 顶栏宽 ≥ 此值 → full */
  fullMin: number;
  /** compactMin ≤ 顶栏宽 < fullMin → compact；再窄 → tight */
  compactMin: number;
}

export interface DensityExtras {
  /** 壳桥在场：右侧多「录制：」「实时字幕」两颗带字开关 */
  shell: boolean;
  /** 英文文案比中文长 */
  english: boolean;
}

/** 顶栏把当前档位发给槽位里的 FilterBar 与右侧开关；默认 full = 没有 HeaderBar 包着时的行为不变 */
export const HeaderDensityContext = createContext<HeaderDensity>("full");

export function useHeaderDensity(): HeaderDensity {
  return useContext(HeaderDensityContext);
}

export function densityForWidth(width: number, thresholds: DensityThresholds): HeaderDensity {
  if (width >= thresholds.fullMin) return "full";
  if (width >= thresholds.compactMin) return "compact";
  return "tight";
}

/** 从元素的计算样式读 tokens.css 的阈值（含 --text-scale 与两个静态加项）；读不到 = null（不判档） */
export function readDensityThresholds(element: Element, extras: DensityExtras): DensityThresholds | null {
  const style = getComputedStyle(element);
  const px = (name: string): number => parseFloat(style.getPropertyValue(name));
  const fullMin = px("--header-density-full-min");
  const compactMin = px("--header-density-compact-min");
  if (!Number.isFinite(fullMin) || !Number.isFinite(compactMin)) return null;
  const scaleRaw = px("--text-scale");
  const scale = Number.isFinite(scaleRaw) && scaleRaw > 0 ? scaleRaw : 1;
  let extra = 0;
  if (extras.shell) extra += px("--header-density-shell-extra") || 0;
  if (extras.english) extra += px("--header-density-en-extra") || 0;
  return { fullMin: (fullMin + extra) * scale, compactMin: (compactMin + extra) * scale };
}

export interface MeasuredDensityOptions {
  /** 测试 / 预览用覆写：给了就不量 */
  override?: HeaderDensity;
  extras: DensityExtras;
  /** 变了就重读阈值（显示偏好的字号档；宽度不变时 ResizeObserver 不会醒） */
  revision?: string;
}

export function useMeasuredHeaderDensity(
  ref: RefObject<HTMLElement | null>,
  { override, extras, revision = "" }: MeasuredDensityOptions,
): HeaderDensity {
  const [measured, setMeasured] = useState<HeaderDensity>("full");
  const { shell, english } = extras;

  useEffect(() => {
    const element = ref.current;
    if (override || !element || typeof ResizeObserver === "undefined") return;
    const thresholds = readDensityThresholds(element, { shell, english });
    if (!thresholds) return;
    const apply = () => setMeasured(densityForWidth(element.getBoundingClientRect().width, thresholds));
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, override, shell, english, revision]);

  return override ?? measured;
}
