// 设置页（CONTRACT §59 + §68 + §54.4；?page=settings 深链，左侧导航栏「设置」；?anchor=<id> 滚到某区）。
// 分区与顺序逐字镜像原生 Settings.swift 的 SettingsSectionDescriptor 注册表（ui/parity/native-inventory.json
// screen:settings.*；§66.2）：通用 · 录制 · 实时字幕 · 笔记库 · 凭证 · Slack 接入 · Gmail 接入 · 导入 Claude Code 工作 ·
// Skills · MCP servers · 同步 / 配对 · 审批 / 成本 · Feature flags · 每周摘要 · 语气档案 · 脱敏 · 产品改进计划 · 开发者 · 开发会话；
// web 自有区（显示 §54.1 第 12 项、模型 §59、通知 §28、素材库 §62、会议纪要 §63、每日整理 §70）插在语义最近的位置。已退役：菜单栏（D3）；
// 同步 / 配对 = SyncSection（§68.15：server 起 act.syncd --pair / --disable，二维码由 syncd 落盘）；「关于」是 sidebar 页
// （?page=about），不再重复。
// 通用区由 server 目录驱动（CatalogSection，文案 server-owned）；页面级只做骨架：返回链接 + 标题 + 目录 + section 列表。
import { useEffect, useState } from "react";
import "../components/chrome/chrome.css";
import "../components/settings/settings.css";
import { CaptionsSection } from "../components/settings/CaptionsSection";
import { CatalogSection } from "../components/settings/CatalogSection";
import { ClaudeImportSection } from "../components/settings/ClaudeImportSection";
import { CredentialsSection } from "../components/settings/CredentialsSection";
import { DailyLoopSection } from "../components/settings/DailyLoopSection";
import { DisplaySection } from "../components/settings/DisplaySection";
import { GeneralExtras } from "../components/settings/GeneralExtras";
import { GmailSection } from "../components/settings/GmailSection";
import { ModelsSection } from "../components/settings/ModelsSection";
import { RecapSection } from "../components/settings/RecapSection";
import { SkillsSection } from "../components/settings/SkillsSection";
import { RecordingSection } from "../components/settings/RecordingSection";
import { MaintainerExtras } from "../components/settings/MaintainerExtras";
import { MaterialsSection } from "../components/settings/MaterialsSection";
import { McpSection } from "../components/settings/McpSection";
import { ObsidianSection } from "../components/settings/ObsidianSection";
import { SlackSection } from "../components/settings/SlackSection";
import { SyncSection } from "../components/settings/SyncSection";
import { VoiceStatus } from "../components/settings/VoiceStatus";
import { useI18n } from "../i18n";
import { buildAppUrl, readAnchor } from "../route";
import { useAppState } from "../store";

/** 目录条目（id = section DOM id 的后缀；顺序 = 页面顺序 = 原生注册表顺序，web 自有区就近插入）。
 *  标题 zh / en 逐字镜像原生 SettingsSectionDescriptor（screen:settings.* 探针读这里）。 */
export const SETTINGS_TOC: Array<{ id: string; zh: string; en: string }> = [
  { id: "display", zh: "显示", en: "Display" },
  { id: "models", zh: "模型", en: "Models" },
  { id: "general", zh: "通用", en: "General" },
  { id: "notifications", zh: "通知", en: "Notifications" },
  { id: "recording", zh: "录制", en: "Recording" },
  { id: "live_captions", zh: "实时字幕", en: "Live captions" },
  { id: "obsidian", zh: "笔记库", en: "Notes vault" },
  { id: "credentials", zh: "凭证（存本机 config/secrets/，保存后自动验证）", en: "Credentials (stored locally in config/secrets/; verified automatically on save)" },
  { id: "slack", zh: "Slack 接入", en: "Slack" },
  { id: "gmail", zh: "Gmail 接入", en: "Gmail" },
  { id: "claude_import", zh: "导入 Claude Code 工作", en: "Import Claude Code work" },
  { id: "skills", zh: "Skills（Claude Code 技能）", en: "Skills (Claude Code)" },
  { id: "mcp", zh: "MCP servers（Claude Code 外接工具）", en: "MCP servers (Claude Code external tools)" },
  { id: "sync", zh: "同步 / 配对", en: "Sync / Pairing" },
  { id: "approval", zh: "审批 / 成本", en: "Approval / Cost" },
  { id: "flags", zh: "Feature flags（§16，默认全开）", en: "Feature flags (§16, all on by default)" },
  { id: "digest", zh: "每周摘要", en: "Weekly digest" },
  { id: "voice", zh: "语气档案（以你的口吻起草）", en: "Voice profile (drafts in your voice)" },
  { id: "redaction", zh: "脱敏（发给 AI 前本地打码）", en: "Redaction (local masking before sending to AI)" },
  { id: "telemetry", zh: "产品改进计划", en: "Product improvement program" },
  { id: "maintainer", zh: "开发者 · 开发会话", en: "Developer session" },
  { id: "materials", zh: "素材库", en: "Materials" },
  { id: "recap", zh: "会议纪要", en: "Recaps" },
  { id: "daily_loop", zh: "每日整理", en: "Daily tidy-up" },
];

/** 原生 SettingsWeeklyDigest 的状态字：开关旁一句「已开启 / 已关闭」（读目录 effective） */
function DigestStatus() {
  const { text } = useI18n();
  const { settingsCatalog } = useAppState();
  const field = settingsCatalog?.sections.find((s) => s.id === "digest")?.fields.find((f) => f.key === "weekly_digest_enabled");
  if (!field) return null;
  return <p className="settings-helper">{field.effective === true ? text("已开启", "Enabled") : text("已关闭", "Disabled")}</p>;
}

/** 原生 Settings.swift 顶部的搜索框（⌘F 聚焦）：按区块正文过滤，全不匹配时说「无匹配设置」 */
function filterSections(query: string): number {
  const q = query.trim().toLowerCase();
  let shown = 0;
  document.querySelectorAll<HTMLElement>(".settings-page > .settings-section, .settings-page > div[id^='settings-']").forEach((el) => {
    const hit = !q || (el.textContent ?? "").toLowerCase().includes(q);
    el.hidden = !hit;
    if (hit) shown += 1;
  });
  return shown;
}

export function SettingsPage() {
  const { text, language } = useI18n();
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState<number | null>(null);

  useEffect(() => {
    setShown(filterSections(query));
  }, [query, language]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey && !event.shiftKey && !event.altKey && event.key.toLowerCase() === "f") {
        event.preventDefault();
        document.getElementById("settings-search")?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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
      <div className="settings-search-row">
        <input
          id="settings-search"
          type="search"
          className="chrome-search settings-search"
          placeholder={text("搜索设置（⌘F）", "Search settings (⌘F)")}
          aria-label={text("搜索设置", "Search settings")}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        {query && <button type="button" className="btn btn-quiet" onClick={() => setQuery("")}>{text("清除", "Clear")}</button>}
        {query && shown === 0 && <span className="settings-helper">{text("无匹配设置", "No matching settings")}</span>}
      </div>
      <nav className="settings-toc" aria-label={text("设置目录", "Settings sections")}>
        {SETTINGS_TOC.map((entry) => (
          <a key={entry.id} href={`#settings-${entry.id}`}>{language === "zh" ? entry.zh : entry.en}</a>
        ))}
      </nav>
      {/* §54.1 第 12 项 显示：字号 / 字重 / 描边三把旋钮，点选即生效（owner 4K 屏「框细字细」） */}
      <div id="settings-display"><DisplaySection /></div>
      <div id="settings-models"><ModelsSection /></div>
      <CatalogSection sectionId="general"><GeneralExtras /></CatalogSection>
      <CatalogSection sectionId="notifications" />
      <RecordingSection />
      <CaptionsSection />
      <ObsidianSection />
      <CredentialsSection />
      <SlackSection />
      <GmailSection />
      <ClaudeImportSection />
      <div id="settings-skills"><SkillsSection /></div>
      <McpSection />
      <SyncSection />
      <CatalogSection sectionId="approval" />
      <CatalogSection sectionId="flags" />
      <CatalogSection sectionId="digest" between={{ weekly_digest_enabled: <DigestStatus /> }} />
      {/* 语气档案：原生 voiceGroup 的「当前生效」状态行 + 打开档案 在开关之前 */}
      <CatalogSection sectionId="voice" lead={<VoiceStatus />} />
      <CatalogSection sectionId="redaction" />
      <CatalogSection sectionId="telemetry" />
      <CatalogSection sectionId="maintainer"><MaintainerExtras /></CatalogSection>
      <div id="settings-materials"><MaterialsSection /></div>
      {/* §63 会议纪要：会后自动出稿 / 默认语言 / Slack 草稿开关（默认关） */}
      <div id="settings-recap"><RecapSection /></div>
      {/* §70 每日整理：开关 / 时刻 / 每天最多几张提案 / 过时天数 / 回收站保留天数 */}
      <div id="settings-daily_loop"><DailyLoopSection /></div>
    </main>
  );
}
