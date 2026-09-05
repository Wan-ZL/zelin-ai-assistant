// Slack 接入区（§15.3 v0.14 / §48 / §68；原生 SettingsSlack.swift 的 web 版，标签逐字镜像）：
// 「启用 Slack 雷达」开关（目录 section slack；区首导语来自 server 目录 help）→ 三步引导 ① 建 Slack app（复制 App
// Manifest = GET /api/slack/manifest 写剪贴板 + 打开 api.slack.com/apps；文件缺席 404 → 原生「找不到 …——repo 不完整？
// 重装一次即可。」）② 安装授权（含管理员审批 / MCP 兜底句）③ 粘贴 token（SecretRow，保存即验证、身份自动填好 = server
// auth.test 回填 owner_slack_user_id）→ 「监控范围」（加载频道和成员 → 勾选器写 slack_channels / watch_people，
// SlackDirectoryPicker；list 字段的文本框仍在，手填 id 也行）→ 「后台雷达」行（launchd agent 状态 / 重新安装）
// +「运行状态（真实轮询结果）」+ 立即测试一轮（RadarAgentPanel，§48.7）+ 健康摘要（§48 投影）。
import { useEffect, useState } from "react";
import { ApiError, fetchSlackManifest } from "../../api";
import { useI18n, type I18n } from "../../i18n";
import { refreshSecrets, useAppState } from "../../store";
import { CatalogSection } from "./CatalogSection";
import { RadarAgentPanel } from "./RadarAgentPanel";
import { SecretRow } from "./SecretRow";
import { SlackDirectoryPicker } from "./SlackDirectoryPicker";
import { HealthLine } from "./sourceHealth";
import { errorMessage } from "./useToast";

/** server/slack_manifest.py 缺文件时的 404 details（repo 相对路径） */
const MANIFEST_REL = "config/slack-app-manifest.json";

/** 「复制 App Manifest」失败句：GET /api/slack/manifest 404（repo 里没有那份文件）→ 原生 copyManifest 的大白话
 *  「找不到 <path>——repo 不完整？重装一次即可。」（路径取 server details.path，缺了就用 repo 相对路径）；其它错误原句。 */
export function manifestErrorMessage(err: unknown, text: I18n["text"]): string {
  if (err instanceof ApiError && err.status === 404) {
    const details = err.details as { path?: unknown } | undefined;
    const path = typeof details?.path === "string" && details.path ? details.path : MANIFEST_REL;
    return text(`找不到 ${path}——repo 不完整？重装一次即可。`, `Missing ${path} — incomplete repo? Reinstall to fix.`);
  }
  return errorMessage(err);
}

export function SlackSection() {
  const { text } = useI18n();
  const { board, secrets } = useAppState();
  const [copied, setCopied] = useState(false);
  const [manifestError, setManifestError] = useState<string | null>(null);
  useEffect(() => {
    if (!secrets) void refreshSecrets();
  }, [secrets]);
  const health = board?.radar_sources?.slack;

  async function copyManifest() {
    setManifestError(null);
    try {
      const { manifest } = await fetchSlackManifest();
      await navigator.clipboard.writeText(manifest);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      setManifestError(manifestErrorMessage(err, text));
    }
  }

  const steps = (
    <ol className="settings-steps">
      <li>
        <strong>{text("① 建 Slack app（一次粘贴，权限已配好）", "① Create the Slack app (one paste; scopes preconfigured)")}</strong>
        <p className="settings-helper">{text("打开 api.slack.com/apps → Create New App → From a manifest → 选你的 workspace → 对话框切到 JSON 标签页 → 粘贴刚复制的内容 → Create。", "Open api.slack.com/apps → Create New App → From a manifest → pick your workspace → switch the dialog to the JSON tab → paste → Create.")}</p>
        <div className="settings-actions">
          <button type="button" className="btn" onClick={() => void copyManifest()}>{copied ? text("已复制 ✓", "Copied ✓") : text("复制 App Manifest", "Copy App Manifest")}</button>
          <a className="btn" href="https://api.slack.com/apps" target="_blank" rel="noreferrer">{text("打开 api.slack.com/apps", "Open api.slack.com/apps")}</a>
        </div>
        {manifestError && <p className="settings-warning" role="alert">{manifestError}</p>}
      </li>
      <li>
        <strong>{text("② 安装授权", "② Install & authorize")}</strong>
        <p className="settings-helper">{text("页面顶部 Install to Workspace → 授权。装好后到左侧 OAuth & Permissions，复制 User OAuth Token（xoxp- 开头；不是 xoxb- 的 Bot token）。公司要求管理员审批的话，等批下来再做第 ③ 步——期间雷达会用只读 MCP 兜底扫描，不会干等。", "Install to Workspace at the top → authorize. Then under OAuth & Permissions copy the User OAuth Token (starts with xoxp-, not the xoxb- bot token). If your company requires admin approval, do step ③ once it's granted — meanwhile the radar falls back to read-only MCP scanning instead of waiting idle.")}</p>
      </li>
      <li>
        <strong>{text("③ 粘贴 token（保存即验证，身份自动填好）", "③ Paste the token (verified on save; identity auto-filled)")}</strong>
        <SecretRow
          name="slack-user-token.txt"
          labelOverride="Slack user token"
          placeholderOverride={text("xoxp-…（只存本机 config/secrets/）", "xoxp-… (stored locally in config/secrets/)")}
          emptyVerifyNote={text("先粘贴并保存 token 再验证", "Paste and save a token first")}
          links={[{ label: text("接入指南", "Setup guide"), href: "https://github.com/Wan-ZL/zelin-ai-assistant/blob/main/docs/SLACK_SETUP.md" }]}
        />
      </li>
    </ol>
  );

  return (
    <CatalogSection sectionId="slack" between={{ slack_enabled: <>{steps}<SlackDirectoryPicker /></> }}>
      <RadarAgentPanel source="slack" />
      {health && <ul className="settings-health"><HealthLine source="slack" health={health} /></ul>}
    </CatalogSection>
  );
}
