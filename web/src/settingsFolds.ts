// 设置页分区的开合记忆（CONTRACT §68.1 追记，D44；原生 Settings.swift `SettingsCollapseStore` 的 web 版）。
// 键名逐字镜像原生 UserDefaults `settings.expandedSections`，落 localStorage；值 = 展开着的 section id 的 JSON 数组
// （排序后写，读时坏形 / 缺席 → 默认集）。原生首跑种子是「全部折叠」；web 是混合式（D44）：常用四区默认展开、
// 其余折叠——「常用」= 通用（语言 / 终端 / 更新，最常拨的旋钮）、依赖检查（这台机器能不能跑，D30 把它排在通用之后
// 就是同一话题）、录制 + 实时字幕（每天开关的两把）。集成类 / 开关表 / 摘要 / 语气档案等设一次就不再动的区折着。
// 只有键**缺席**才用默认集：用户把四区全折起来存的是 `[]`，下次仍是全折——记忆比默认大。
// 搜索 / ?anchor= 深链的强制展开不写这里（搜索期间 toggle 禁用、状态不动，原生 §1.9 同款；anchor 与原生一样 expand 并记住）。

export const EXPANDED_SECTIONS_STORAGE_KEY = "settings.expandedSections";

/** 默认展开的四区（truth = 本常量；SettingsPage.SETTINGS_TOC 里的 id） */
export const DEFAULT_EXPANDED_SECTIONS: readonly string[] = ["general", "deps", "recording", "live_captions"];

/** 读记忆：缺席 / 不可读 / 坏形 → 默认集；合法 JSON 数组（含空数组）原样收，非字串项丢弃 */
export function readExpandedSections(): ReadonlySet<string> {
  try {
    const raw = window.localStorage.getItem(EXPANDED_SECTIONS_STORAGE_KEY);
    if (raw === null) return new Set(DEFAULT_EXPANDED_SECTIONS);
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set(DEFAULT_EXPANDED_SECTIONS);
    return new Set(parsed.filter((item): item is string => typeof item === "string" && item.length > 0));
  } catch {
    return new Set(DEFAULT_EXPANDED_SECTIONS);
  }
}

/** 写记忆（排序后的 JSON 数组；写失败静默——本次会话仍然生效，原生 persist 同款） */
export function writeExpandedSections(expanded: ReadonlySet<string>): void {
  try {
    window.localStorage.setItem(EXPANDED_SECTIONS_STORAGE_KEY, JSON.stringify([...expanded].sort()));
  } catch {
    /* 隐私模式等 localStorage 不可写 */
  }
}

/** 翻一区的开合（纯函数：返回新集） */
export function toggledSections(expanded: ReadonlySet<string>, id: string): ReadonlySet<string> {
  const next = new Set(expanded);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}
