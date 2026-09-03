// 显示偏好的落地（CONTRACT §54.1 第 12 项）：server `ui.display` 的三个值 → <html> 上的三个
// data-* 属性（data-text-size / data-text-weight / data-stroke）。值 → CSS 变量（--text-scale /
// --weight-shift / --stroke-w）的映射只住 tokens.css，这里不知道任何 px / 倍率——单源纪律。
// 与主题同一套首帧机制：写 localStorage["zai.display"]，index.html 首帧脚本读它预写同一组属性，
// 避免 server 快照到达前字号跳变；server 快照永远是真源，到达即覆盖。
import type { DisplaySettings } from "./types";

export const DISPLAY_STORAGE_KEY = "zai.display";

/** 三把旋钮的 wire 键 → <html> dataset 键（dataset 的 camelCase ⇄ data-text-size 等） */
export const DISPLAY_FIELDS = {
  text_size: "textSize",
  text_weight: "textWeight",
  stroke: "stroke",
} as const;

export type DisplayField = keyof typeof DISPLAY_FIELDS;

export type DisplayPrefs = Record<DisplayField, string>;

/** 从 server 快照取三键（其余 wire 键不进 dataset） */
export function prefsOf(settings: Pick<DisplaySettings, DisplayField>): DisplayPrefs {
  return { text_size: settings.text_size, text_weight: settings.text_weight, stroke: settings.stroke };
}

/** 三个属性写到 <html>；同时缓存给下一次首帧。空值 = 删属性（tokens.css 的默认档接手）。 */
export function applyDisplayPrefs(prefs: DisplayPrefs, root: HTMLElement = document.documentElement): void {
  for (const field of Object.keys(DISPLAY_FIELDS) as DisplayField[]) {
    const value = prefs[field];
    const key = DISPLAY_FIELDS[field];
    if (typeof value === "string" && value) {
      root.dataset[key] = value;
    } else {
      delete root.dataset[key];
    }
  }
  try {
    window.localStorage.setItem(DISPLAY_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    /* localStorage 不可写：本次会话仍生效，仅首帧缓存缺席 */
  }
}

/** 当前 <html> 上生效的三个属性（测试与预览用；缺席的键为空串） */
export function readAppliedDisplayPrefs(root: HTMLElement = document.documentElement): DisplayPrefs {
  return {
    text_size: root.dataset.textSize ?? "",
    text_weight: root.dataset.textWeight ?? "",
    stroke: root.dataset.stroke ?? "",
  };
}
