// 笔记库区（§48 / §68；原生 Settings.swift obsidianGroup 的 web 版）：「启用 Obsidian 雷达」+「Obsidian Vault 位置」
// （目录 section obsidian）+ 来源健康一行。原生的 选择… / 打开 / 创建 三颗按钮是 NSOpenPanel / Finder 的事，
// 浏览器里没有文件对话框——路径手填，健康行会告诉你目录在不在（vault_missing）。
import { useI18n } from "../../i18n";
import { useAppState } from "../../store";
import { CatalogSection } from "./CatalogSection";
import { HealthLine } from "./sourceHealth";

export function ObsidianSection() {
  const { text } = useI18n();
  const { board } = useAppState();
  const health = board?.radar_sources?.obsidian;
  return (
    <CatalogSection sectionId="obsidian">
      {health && (
        <ul className="settings-health" aria-label={text("来源健康", "Source health")}>
          <HealthLine source="obsidian" health={health} />
        </ul>
      )}
    </CatalogSection>
  );
}
