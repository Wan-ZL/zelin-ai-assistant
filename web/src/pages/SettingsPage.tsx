// 设置页（CONTRACT §59 + §68；?page=settings 深链，顶栏齿轮入口；?anchor=<id> 滚到某区）。
// 原生 Settings.swift 的 20 个区在 web 的落点（D3 退役前的 parity 清单，§54.1 / §68.1）：
//   模型（§59，ModelsSection）· 来源开关 + 凭证（sources）· 录制（桥）· 实时字幕（桥 + 凭证）·
//   通知 · 产品改进计划 · 摘要与回顾 · 通用 · 审批 / 成本 · Feature flags · 脱敏 · 语气档案 ·
//   开发者会话（以上通用区由 server 目录驱动，CatalogSection）· 导入 Claude Code 工作 · MCP servers · Skills（D13，§67，SkillsSection）·
//   素材库（D11，§62，MaterialsSection）· 会议纪要（§63，RecapSection）· 每日整理（D10，§70，DailyLoopSection）· 关于 / 看板 app。
//   已删（Dock-only 决策 D3）：菜单栏图标开关；同步 / 配对（iPhone 联动）随 §31 syncd 面另议。
// 页面级只做骨架：返回链接 + 标题 + 目录 + section 列表；每个 section 自己拉自己的数据（经 store action）。
import { useEffect } from "react";
import "../components/chrome/chrome.css";
import "../components/settings/settings.css";
import { AboutSection } from "../components/settings/AboutSection";
import { CaptionsSection } from "../components/settings/CaptionsSection";
import { CatalogSection } from "../components/settings/CatalogSection";
import { ClaudeImportSection } from "../components/settings/ClaudeImportSection";
import { DailyLoopSection } from "../components/settings/DailyLoopSection";
import { ModelsSection } from "../components/settings/ModelsSection";
import { RecapSection } from "../components/settings/RecapSection";
import { SkillsSection } from "../components/settings/SkillsSection";
import { RecordingSection } from "../components/settings/RecordingSection";
import { MaterialsSection } from "../components/settings/MaterialsSection";
import { McpSection } from "../components/settings/McpSection";
import { SourcesSection } from "../components/settings/SourcesSection";
import { useI18n } from "../i18n";
import { buildAppUrl, readAnchor } from "../route";

/** 目录条目（id = section DOM id 的后缀；顺序 = 页面顺序） */
export const SETTINGS_TOC: Array<{ id: string; zh: string; en: string }> = [
  { id: "models", zh: "模型", en: "Models" },
  { id: "sources", zh: "来源开关与凭证", en: "Sources & credentials" },
  { id: "recording", zh: "录制", en: "Recording" },
  { id: "live_captions", zh: "实时字幕", en: "Live captions" },
  { id: "notifications", zh: "通知", en: "Notifications" },
  { id: "telemetry", zh: "产品改进计划", en: "Product improvement" },
  { id: "digest", zh: "摘要与回顾", en: "Digests" },
  { id: "general", zh: "通用", en: "General" },
  { id: "approval", zh: "审批 / 成本", en: "Approval / Cost" },
  { id: "flags", zh: "Feature flags", en: "Feature flags" },
  { id: "redaction", zh: "脱敏", en: "Redaction" },
  { id: "voice", zh: "语气档案", en: "Voice profile" },
  { id: "maintainer", zh: "开发者会话", en: "Developer session" },
  { id: "claude_import", zh: "导入 Claude Code 工作", en: "Import Claude Code work" },
  { id: "skills", zh: "Skills", en: "Skills" },
  { id: "mcp", zh: "MCP servers", en: "MCP servers" },
  { id: "materials", zh: "素材库", en: "Materials" },
  { id: "recap", zh: "会议纪要", en: "Recaps" },
  { id: "daily_loop", zh: "每日整理", en: "Daily tidy-up" },
  { id: "about", zh: "关于 / 看板 app", en: "About / Board app" },
];

export function SettingsPage() {
  const { text, language } = useI18n();

  // ?anchor= 深链（字幕悬浮窗齿轮 → live_captions）：section 挂载后滚过去并高亮一下
  useEffect(() => {
    const anchor = readAnchor(window.location.search);
    if (!anchor) return undefined;
    const el = document.getElementById(`settings-${anchor}`);
    if (!el) return undefined;
    el.scrollIntoView({ block: "start" });
    el.classList.add("is-anchored");
    const timer = window.setTimeout(() => el.classList.remove("is-anchored"), 2500);
    return () => window.clearTimeout(timer);
  }, []);

  return (
    <main className="settings-page">
      <a className="trash-back-link" href={buildAppUrl(window.location.href, "board", null).toString()}>
        {text("← 返回看板", "← Back to board")}
      </a>
      <div className="settings-page-head">
        <h2 className="settings-page-title">{text("设置", "Settings")}</h2>
        <span className="settings-helper">{text("写的是 state/settings_overrides.json，config.yaml 原样不动；等于 config 的值不落键。", "Writes state/settings_overrides.json and never edits config.yaml; a value equal to config leaves no key behind.")}</span>
      </div>
      <nav className="settings-toc" aria-label={text("设置目录", "Settings sections")}>
        {SETTINGS_TOC.map((entry) => (
          <a key={entry.id} href={`#settings-${entry.id}`}>{language === "zh" ? entry.zh : entry.en}</a>
        ))}
      </nav>
      <div id="settings-models"><ModelsSection /></div>
      <SourcesSection />
      <RecordingSection />
      <CaptionsSection />
      <CatalogSection sectionId="notifications" />
      <CatalogSection sectionId="telemetry" />
      <CatalogSection sectionId="digest" />
      <CatalogSection sectionId="general" />
      <CatalogSection sectionId="approval" />
      <CatalogSection sectionId="flags" />
      <CatalogSection sectionId="redaction" />
      <CatalogSection sectionId="voice" />
      <CatalogSection sectionId="maintainer" />
      <ClaudeImportSection />
      <McpSection />
      <div id="settings-materials"><MaterialsSection /></div>
      {/* §63 会议纪要：会后自动出稿 / 默认语言 / Slack 草稿开关（默认关） */}
      <div id="settings-recap"><RecapSection /></div>
      {/* §70 每日整理：开关 / 时刻 / 每天最多几张提案 / 过时天数 / 回收站保留天数 */}
      <div id="settings-daily_loop"><DailyLoopSection /></div>
      <div id="settings-skills"><SkillsSection /></div>
      <AboutSection />
    </main>
  );
}
