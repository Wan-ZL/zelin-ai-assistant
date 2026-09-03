// Slack 接入区（§15.3 v0.14 / §48 / §68；原生 SettingsSlack.swift 的 web 版，标签逐字镜像）：
// 「启用 Slack 雷达」开关（目录 section slack）→ 三步引导 ① 建 Slack app（复制 App Manifest = GET /api/slack/manifest
// 写剪贴板 + 打开 api.slack.com/apps）② 安装授权 ③ 粘贴 token（SecretRow，保存即验证、身份自动填好 = server
// auth.test 回填 owner_slack_user_id）→ 「监控范围」（加载频道和成员 → 勾选器写 slack_channels / watch_people，
// SlackDirectoryPicker；list 字段的文本框仍在，手填 id 也行）→ 「后台雷达」行（launchd agent 状态 / 重新安装）
// +「运行状态（真实轮询结果）」+ 立即测试一轮（RadarAgentPanel，§48.7）+ 健康摘要（§48 投影）。
import { useEffect, useState } from "react";
import { fetchSlackManifest } from "../../api";
import { useI18n } from "../../i18n";
import { refreshSecrets, useAppState } from "../../store";
import { CatalogSection } from "./CatalogSection";
import { RadarAgentPanel } from "./RadarAgentPanel";
import { SecretRow } from "./SecretRow";
import { SlackDirectoryPicker } from "./SlackDirectoryPicker";
import { HealthLine } from "./sourceHealth";
import { errorMessage } from "./useToast";

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
      setManifestError(errorMessage(err));
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
        <p className="settings-helper">{text("页面顶部 Install to Workspace → 授权。装好后到左侧 OAuth & Permissions，复制 User OAuth Token（xoxp- 开头；不是 xoxb- 的 Bot token）。", "Install to Workspace at the top → authorize. Then under OAuth & Permissions copy the User OAuth Token (starts with xoxp-, not the xoxb- bot token).")}</p>
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
