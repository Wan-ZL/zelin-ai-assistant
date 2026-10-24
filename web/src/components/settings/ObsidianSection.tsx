// 笔记库区（§68；原生 Settings.swift obsidianGroup 的 web 版）：「Obsidian Vault 位置」（目录 section obsidian；目录字段 →
// FieldControl 长出 选择… / 打开 / 创建：壳里 NSOpenPanel 经 §61.1 桥，浏览器退化成路径框，§68.1）。
// v2.0.0：「启用 Obsidian 雷达」与来源健康行随 Act 管线的雷达退役（全文见 tag full）——vault 只是 ingest 的落点。
import { CatalogSection } from "./CatalogSection";

export function ObsidianSection() {
  return <CatalogSection sectionId="obsidian" />;
}
