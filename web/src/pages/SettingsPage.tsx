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
// 分区开合（D44，§68.1 追记；原生 Settings.swift SettingsCollapseStore + CollapsibleSection 的混合式 web 版）：每区包在
// SettingsFold 里（区头 = aria-expanded 按钮，正文常挂载、折叠时 hidden——草稿与搜索干草都不丢）；默认展开 通用 / 依赖检查 /
// 录制 / 实时字幕（settingsFolds.DEFAULT_EXPANDED_SECTIONS），其余折叠；记忆 = store.expandedSettingsSections ↔ localStorage
// settings.expandedSections；搜索命中的区强制展开（toggle 禁用、记忆不动）；?anchor= / #settings-<id> 深链与目录点击 expand
// 并记住；目录条目 data-expanded 反映状态。锚点 `#settings-<id>` 落在 fold 壳上（永远可见，折着也滚得到）。深链锚点是一次性
// 指令：每次 URL 带来（挂载时、或 D40 换页不重载下已在设置页时又点了一条带 anchor 的链接）消费一次，读完就从 URL 上摘掉
// （route.withoutSettingsAnchor，经 route.navigate replace）——不摘就后退 / 前进回到那条历史时重新展开 + 记住 + 滚动，把用户手动
// 折起的区又翻开；目录点击自己滚（preventDefault），不留 hash。
import { useEffect, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";
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
import { SettingsFold } from "../components/settings/SettingsFold";
import { MaintainerExtras } from "../components/settings/MaintainerExtras";
import { MaterialsSection } from "../components/settings/MaterialsSection";
import { McpSection } from "../components/settings/McpSection";
import { ObsidianSection } from "../components/settings/ObsidianSection";
import { SlackSection } from "../components/settings/SlackSection";
import { SyncSection } from "../components/settings/SyncSection";
import { VoiceGenerate } from "../components/settings/VoiceGenerate";
import { VoiceStatus } from "../components/settings/VoiceStatus";
import { useI18n } from "../i18n";
import { buildAppUrl, navigate, readSettingsAnchor, useRoute, withoutSettingsAnchor } from "../route";
import { expandSettingsSection, toggleSettingsSection, useAppState } from "../store";
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

/** 每区的 fold 壳（SettingsFold：id `settings-<id>`、data-section=<id>）——搜索过滤 / 晚到正文观察都以它为单位 */
const SECTION_SELECTOR = ".settings-page > .settings-fold";

/** `#settings-<id>` 片段（§68.15 的 `?page=settings#settings-sync` 深链 / 目录条目的 href 被新标签打开）→ 目录里的 id；其它形当没有 */
export function readHashSection(hash: string): string | null {
  const match = /^#settings-([a-z0-9_-]{1,40})$/i.exec(hash);
  return match && SETTINGS_TOC.some((entry) => entry.id === match[1]) ? match[1] : null;
}

/** 滚到一区的 fold 壳（永远可见，折着也滚得到；壳上的 scroll-margin-top 留出顶栏） */
function scrollToFold(id: string): HTMLElement | null {
  const el = document.getElementById(`settings-${id}`);
  el?.scrollIntoView({ block: "start" });
  return el;
}

/** 原生 Settings.swift 顶部的搜索框（⌘F 聚焦）：逐区按双语干草过滤，全不匹配时说「无匹配设置」 */
function filterSections(query: string, catalog: SettingsCatalog | null, secrets: SecretsStatus | null): number {
  let shown = 0;
  document.querySelectorAll<HTMLElement>(SECTION_SELECTOR).forEach((el) => {
    const id = el.dataset.section ?? el.id.replace(/^settings-/, "");
    const secretNames = Array.from(el.querySelectorAll<HTMLElement>("[data-secret]"), (row) => row.dataset.secret ?? "");
    const hit = matchesSearch(sectionHaystack(id, el.textContent ?? "", catalog, secrets, secretNames), query);
    el.hidden = !hit;
    if (hit) shown += 1;
  });
  return shown;
}

/** 一区的开合壳：标题取目录条目（与目录同源，zh / en 随 UI 语言），开合读 store 记忆，toggle 写 store（持久化在 store 动作里） */
function Fold({ id, isForced, children }: { id: string; isForced: boolean; children: ReactNode }) {
  const { language } = useI18n();
  const { expandedSettingsSections } = useAppState();
  const entry = SETTINGS_TOC.find((candidate) => candidate.id === id);
  const title = entry ? (language === "zh" ? entry.zh : entry.en) : id;
  return (
    <SettingsFold id={id} title={title} isExpanded={expandedSettingsSections.has(id)} isForced={isForced} onToggle={toggleSettingsSection}>
      {children}
    </SettingsFold>
  );
}

export function SettingsPage() {
  const { text, language } = useI18n();
  const { settingsCatalog, secrets, expandedSettingsSections } = useAppState();
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState<number | null>(null);
  const catalogReady = settingsCatalog !== null;
  const searchActive = query.trim().length > 0; // 原生 searchActive：命中的区强制展开、toggle 禁用

  // 原生 SwiftUI 每次 body 重算都重跑 matches()，晚到的数据自己浮出命中的区。web 先同步过一遍；有查询时再盯住各区的子树——
  // 目录区在草稿对齐 effect 之后的下一帧才渲 field 与凭证行（store 拿到目录那一拍 data-secret 还不在 DOM），Skills / MCP 等区
  // 各拉各的快照——正文一长出来就在下一帧重过滤一次（只看增删与文本，不看属性：hidden 的翻转本身不触发）
  useEffect(() => {
    setShown(filterSections(query, settingsCatalog, secrets));
    if (!query.trim()) return undefined;
    let frame = 0;
    const observer = new MutationObserver(() => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        setShown(filterSections(query, settingsCatalog, secrets));
      });
    });
    document.querySelectorAll<HTMLElement>(SECTION_SELECTOR).forEach((el) => {
      observer.observe(el, { childList: true, characterData: true, subtree: true });
    });
    return () => {
      observer.disconnect();
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [query, language, settingsCatalog, secrets]);

  // 原生 SettingsSearchField.esc：输入法候选期间 Esc 归输入法（§41 IME 红线）；有字 → 第一下清空；没字 → 第二下交还光标
  function onSearchKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Escape" || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (query) setQuery("");
    else event.currentTarget.blur();
  }

  // 目录条目：展开并记住，再滚到壳；不让浏览器导航到 #settings-<id>（留下的 hash 会在下次挂载时被当深链重放）
  function onTocClick(event: ReactMouseEvent<HTMLAnchorElement>, id: string) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    expandSettingsSection(id);
    scrollToFold(id);
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

  // ?anchor= 深链（字幕悬浮窗齿轮 → live_captions；?page=deps / diagnostics 旧深链 → deps）与 #settings-<id> 片段 = URL 带来的
  // **一次性指令**：到达就消费——强制展开（并记住——原生 expandAnchorIfPending 的 collapse.expand）、滚过去、高亮一下——随即从 URL
  // 上摘掉（`route.navigate(…, true)` = replaceState 不进历史栈、经路由器通知订阅者，useRoute 的快照与 URL 一致；rail 来回 / 后退
  // 前进都不重放）。D40 换页不重载：已在设置页时点一条带 anchor 的链接（录制页 → 依赖检查区、悬浮窗齿轮再点一次）本组件不重挂——
  // 锚点从路由订阅里读，每次到达记一笔 seq（同一个 id 再来也再滚一次）；#settings-<id> 片段只在挂载时读一次（片段不在 useRoute 的
  // 快照里，目录点击自己滚、不留 hash）。server 目录到达会把上方的目录驱动区（通用…）从占位撑成全高、把目标区顶出视口——目录落地
  // 后再对准一次（pending 留着，不随摘掉的 URL 消失）。高亮记在 data-anchored（React 不管的属性）：壳的 className 随 expand 重渲时
  // 会把 imperative 加的 class 抹掉
  const routeAnchor = readSettingsAnchor(useRoute());
  const [pending, setPending] = useState<{ id: string; seq: number } | null>(() => {
    const hashSection = readHashSection(window.location.hash);
    return hashSection ? { id: hashSection, seq: 0 } : null;
  });
  useEffect(() => {
    if (routeAnchor) setPending((prev) => ({ id: routeAnchor, seq: (prev?.seq ?? 0) + 1 }));
  }, [routeAnchor]);
  useEffect(() => {
    if (!pending) return undefined;
    const stripped = withoutSettingsAnchor(window.location.href);
    if (stripped.href !== window.location.href) navigate(stripped, true);
    if (SETTINGS_TOC.some((entry) => entry.id === pending.id)) expandSettingsSection(pending.id);
    const el = scrollToFold(pending.id);
    if (!el) return undefined;
    el.dataset.anchored = "";
    const timer = window.setTimeout(() => { delete el.dataset.anchored; }, 2500);
    return () => window.clearTimeout(timer);
  }, [pending, catalogReady]);

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
      {/* 目录反映开合（data-expanded；搜索期间一律 true）；点条目 = 深链语义：展开并记住、滚到 fold 壳（永远可见）——自己滚、不让浏览器
          留下 #settings-<id>（否则 rail 来回时当深链重放）；带修饰键的点击（新标签 / 新窗口）交给浏览器，新标签里由片段深链自己展开 */}
      <nav className="settings-toc" aria-label={text("设置目录", "Settings sections")}>
        {SETTINGS_TOC.map((entry) => (
          <a
            key={entry.id}
            href={`#settings-${entry.id}`}
            data-expanded={searchActive || expandedSettingsSections.has(entry.id)}
            onClick={(event) => onTocClick(event, entry.id)}
          >
            {language === "zh" ? entry.zh : entry.en}
          </a>
        ))}
      </nav>
      {/* §54.1 第 12 项 显示：字号 / 字重 / 描边三把旋钮，点选即生效（owner 4K 屏「框细字细」） */}
      <Fold id="display" isForced={searchActive}><DisplaySection /></Fold>
      <Fold id="models" isForced={searchActive}><ModelsSection /></Fold>
      <Fold id="general" isForced={searchActive}><CatalogSection sectionId="general"><GeneralExtras /></CatalogSection></Fold>
      {/* D30 依赖检查：原生 DepsView 整段（快速行 / 雷达健康 / 诊断 + web 自有的活性 / 部署 / 安装回执 / 日志）折进设置页 */}
      <Fold id="deps" isForced={searchActive}><DepsSection /></Fold>
      <Fold id="notifications" isForced={searchActive}><CatalogSection sectionId="notifications" /></Fold>
      <Fold id="recording" isForced={searchActive}><RecordingSection /></Fold>
      <Fold id="live_captions" isForced={searchActive}><CaptionsSection /></Fold>
      <Fold id="obsidian" isForced={searchActive}><ObsidianSection /></Fold>
      <Fold id="credentials" isForced={searchActive}><CredentialsSection /></Fold>
      <Fold id="slack" isForced={searchActive}><SlackSection /></Fold>
      <Fold id="gmail" isForced={searchActive}><GmailSection /></Fold>
      <Fold id="claude_import" isForced={searchActive}><ClaudeImportSection /></Fold>
      <Fold id="skills" isForced={searchActive}><SkillsSection /></Fold>
      <Fold id="mcp" isForced={searchActive}><McpSection /></Fold>
      <Fold id="sync" isForced={searchActive}><SyncSection /></Fold>
      <Fold id="approval" isForced={searchActive}><CatalogSection sectionId="approval" /></Fold>
      <Fold id="flags" isForced={searchActive}><CatalogSection sectionId="flags" /></Fold>
      {/* 每周摘要：原生 SettingsWeeklyDigest 的顺序——开关 → 状态字 → 「现在生成一份」+ 回执句；状态摘要频率是 web 自有旋钮 */}
      <Fold id="digest" isForced={searchActive}>
        <CatalogSection sectionId="digest" between={{ weekly_digest_enabled: <><DigestStatus /><DigestExtras /></> }} />
      </Fold>
      {/* 语气档案：原生 voiceGroup 的「当前生效」状态行 + 打开档案 在开关之前 */}
      <Fold id="voice" isForced={searchActive}><CatalogSection sectionId="voice" lead={<VoiceStatus />}><VoiceGenerate /></CatalogSection></Fold>
      <Fold id="redaction" isForced={searchActive}><CatalogSection sectionId="redaction" /></Fold>
      <Fold id="telemetry" isForced={searchActive}><CatalogSection sectionId="telemetry" /></Fold>
      <Fold id="maintainer" isForced={searchActive}><CatalogSection sectionId="maintainer"><MaintainerExtras /></CatalogSection></Fold>
      <Fold id="materials" isForced={searchActive}><MaterialsSection /></Fold>
      {/* §63 会议纪要：会后自动出稿 / 默认语言 / Slack 草稿开关（默认关） */}
      <Fold id="recap" isForced={searchActive}><RecapSection /></Fold>
      {/* §70 每日整理：开关 / 时刻 / 每天最多几张提案 / 过时天数 / 回收站保留天数 */}
      <Fold id="daily_loop" isForced={searchActive}><DailyLoopSection /></Fold>
    </main>
  );
}
