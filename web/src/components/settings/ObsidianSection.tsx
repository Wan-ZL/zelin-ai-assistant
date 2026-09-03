// 笔记库区（§48 / §68；原生 Settings.swift obsidianGroup 的 web 版）：「启用 Obsidian 雷达」+「Obsidian Vault 位置」
// （目录 section obsidian；目录字段 → FieldControl 长出 选择… / 打开 / 创建：壳里 NSOpenPanel 经 §61.1 桥，
// 浏览器退化成路径框，§68.1）+ 来源健康一行（vault_missing 由雷达自己报）。
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
