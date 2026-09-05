// 设置页（CONTRACT §59 + §68 + §54.4；?page=settings 深链，左侧导航栏「设置」；?anchor=<id> 滚到某区；
// ?page=deps / diagnostics 旧深链也开到这里并滚到「依赖检查」区——D30）。
// 分区与顺序逐字镜像原生 Settings.swift 的 SettingsSectionDescriptor 注册表（ui/parity/native-inventory.json
// screen:settings.*；§66.2）：通用 · 录制 · 实时字幕 · 笔记库 · 凭证 · Slack 接入 · Gmail 接入 · 导入 Claude Code 工作 ·
// Skills · MCP servers · 同步 / 配对 · 审批 / 成本 · Feature flags · 每周摘要 · 语气档案 · 脱敏 · 产品改进计划 · 开发者 · 开发会话；
// web 自有区（显示 §54.1 第 12 项、模型 §59、通知 §28、素材库 §62、会议纪要 §63、每日整理 §70）插在语义最近的位置。
// 依赖检查（原生 rail 页 DepsView，D30 2026-09-04 owner「合并到 setting里面」）紧跟通用区——它管的是这台机器能不能跑，
// 与通用区的「初始设置向导 / 权限体检」两行同一话题。已退役：菜单栏（D3）；
// 同步 / 配对 = SyncSection（§68.15：server 起 act.syncd --pair / --disable，二维码由 syncd 落盘）；「关于」是 sidebar 页
// （?page=about），不再重复。
// 通用区由 server 目录驱动（CatalogSection，文案 server-owned）；页面级只做骨架：返回链接 + 标题 + 目录 + section 列表。
// 搜索框（原生 Settings.swift SettingsSearchField + matches()，§54.4 / §68.1 追记）：干草 = 目录标题 zh+en + server 目录该区的
// label / help zh+en（不看 UI 语言）+ 该区凭证行的双语 label + 渲染正文；查询按空白切 token、全部命中才算（AND）；
// Esc 第一下清空、第二下交还光标，输入法候选期间不拦（§41 IME 红线同款）。
import { useEffect, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import "../components/chrome/chrome.css";
import "../components/settings/settings.css";
import { CaptionsSection } from "../components/settings/CaptionsSection";
import { CatalogSection } from "../components/settings/CatalogSection";
import { ClaudeImportSection } from "../components/settings/ClaudeImportSection";
import { CredentialsSection } from "../components/settings/CredentialsSection";
import { DailyLoopSection } from "../components/settings/DailyLoopSection";
import { DepsSection } from "../components/settings/DepsSection";
import { DigestExtras } from "../components/settings/DigestExtras";
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
import { buildAppUrl, readSettingsAnchor } from "../route";
import { useAppState } from "../store";
import type { SecretsStatus, SettingsCatalog } from "../types";

/** 目录条目（id = section DOM id 的后缀；顺序 = 页面顺序 = 原生注册表顺序，web 自有区就近插入）。
 *  标题 zh / en 逐字镜像原生 SettingsSectionDescriptor（screen:settings.* 探针读这里）。 */
export const SETTINGS_TOC: Array<{ id: string; zh: string; en: string }> = [
  { id: "display", zh: "显示", en: "Display" },
  { id: "models", zh: "模型", en: "Models" },
  { id: "general", zh: "通用", en: "General" },
  { id: "deps", zh: "依赖检查", en: "Dependencies" },
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

/** 原生 matches() 的 fold：大小写 + 变音符不敏感（NFD 拆开再去掉组合符），两边都过一遍 */
export function foldSearchText(value: string): string {
  return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

/** 原生 matches()：查询按空白切 token，每个 token 都得是干草的子串（AND）；空查询 = 全部可见 */
export function matchesSearch(haystack: string, query: string): boolean {
  const tokens = foldSearchText(query).split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return true;
  const hay = foldSearchText(haystack);
  return tokens.every((token) => hay.includes(token));
}

/** 一区的双语干草（原生 titleZh + titleEn + keywords 的 web 版）：目录标题 zh+en；server 目录同 id 区的标题 / help /
 *  每个 field 的 label + help（zh+en 都进，不看 UI 语言——原生 keywords 就是双语 blob）；这一区里渲染着的凭证行
 *  （SecretRow 的 data-secret，落点由 DOM 说）的双语 label；最后是当前渲染出的正文（web 自有区的旋钮文案只在 DOM 里）。 */
export function sectionHaystack(id: string, rendered: string, catalog: SettingsCatalog | null, secrets: SecretsStatus | null,
  secretNames: readonly string[] = []): string {
  const parts: string[] = [];
  const toc = SETTINGS_TOC.find((entry) => entry.id === id);
  if (toc) parts.push(toc.zh, toc.en);
  const section = catalog?.sections.find((s) => s.id === id);
  if (section) {
    parts.push(section.title.zh, section.title.en, section.help.zh, section.help.en);
    for (const field of section.fields) parts.push(field.label.zh, field.label.en, field.help.zh, field.help.en);
  }
  for (const row of secrets?.secrets ?? []) if (secretNames.includes(row.name)) parts.push(row.label.zh, row.label.en);
  parts.push(rendered);
  return parts.filter(Boolean).join(" ");
}

/** 原生 Settings.swift 顶部的搜索框（⌘F 聚焦）：逐区按双语干草过滤，全不匹配时说「无匹配设置」 */
function filterSections(query: string, catalog: SettingsCatalog | null, secrets: SecretsStatus | null): number {
  let shown = 0;
  document.querySelectorAll<HTMLElement>(".settings-page > .settings-section, .settings-page > div[id^='settings-']").forEach((el) => {
    const id = el.id.replace(/^settings-/, "");
    const secretNames = Array.from(el.querySelectorAll<HTMLElement>("[data-secret]"), (row) => row.dataset.secret ?? "");
    const hit = matchesSearch(sectionHaystack(id, el.textContent ?? "", catalog, secrets, secretNames), query);
    el.hidden = !hit;
    if (hit) shown += 1;
  });
  return shown;
}

export function SettingsPage() {
  const { text, language } = useI18n();
  const { settingsCatalog, secrets } = useAppState();
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState<number | null>(null);
  const catalogReady = settingsCatalog !== null;

  useEffect(() => {
    setShown(filterSections(query, settingsCatalog, secrets));
  }, [query, language, settingsCatalog, secrets]);

  // 原生 SettingsSearchField.esc：输入法候选期间 Esc 归输入法（§41 IME 红线）；有字 → 第一下清空；没字 → 第二下交还光标
  function onSearchKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Escape" || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (query) setQuery("");
    else event.currentTarget.blur();
  }

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

  // ?anchor= 深链（字幕悬浮窗齿轮 → live_captions；?page=deps / diagnostics 旧深链 → deps）：section 挂载后滚过去并高亮一下；
  // server 目录到达会把上方的目录驱动区（通用…）从占位撑成全高、把目标区顶出视口——目录落地后再对准一次
  useEffect(() => {
    const anchor = readSettingsAnchor(window.location.search);
    if (!anchor) return undefined;
    const el = document.getElementById(`settings-${anchor}`);
    if (!el) return undefined;
    el.scrollIntoView({ block: "start" });
    el.classList.add("is-anchored");
    const timer = window.setTimeout(() => el.classList.remove("is-anchored"), 2500);
    return () => window.clearTimeout(timer);
  }, [catalogReady]);

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
          onKeyDown={onSearchKeyDown}
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
      {/* D30 依赖检查：原生 DepsView 整段（快速行 / 雷达健康 / 诊断 + web 自有的活性 / 部署 / 安装回执 / 日志）折进设置页 */}
      <DepsSection />
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
      {/* 每周摘要：原生 SettingsWeeklyDigest 的顺序——开关 → 状态字 → 「现在生成一份」+ 回执句；状态摘要频率是 web 自有旋钮 */}
      <CatalogSection sectionId="digest" between={{ weekly_digest_enabled: <><DigestStatus /><DigestExtras /></> }} />
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
