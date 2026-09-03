// 来源开关区（§48 / §68）：目录 section「sources」的三把开关 + 地址/目录字段（CatalogSection 通用渲染），
// 尾部装饰：每源的真源健康摘要（board.radar_sources：enabled / last_ok / skip_reason / stale——
// 「生效 = flag × 开关」的投影，原生 DiagnosticsRules.effectiveSourceEnabled 读的同一份）
// + 三条凭证行（Anthropic / Slack / Gmail，写 config/secrets/，值 write-only）。
import { useEffect } from "react";
import { useI18n } from "../../i18n";
import { RelativeTime } from "../board/cardChrome";
import { refreshSecrets, useAppState } from "../../store";
import type { RadarSourceHealth } from "../../types";
import { CatalogSection } from "./CatalogSection";
import { SecretRow } from "./SecretRow";

type Text = (zh: string, en: string) => string;

/** §48 skip_reason 闭集词表 → 一句人话（未知原样） */
export function skipReasonLabel(reason: string, text: Text): string {
  const table: Record<string, [string, string]> = {
    no_credentials: ["没有凭证——在下方粘贴并保存", "No credential — paste and save it below"],
    no_address: ["没有邮箱地址——填上方「Gmail 地址」", "No address — fill in Gmail address above"],
    auth_failed: ["登录被拒——应用专用密码过期或地址不对", "Login rejected — app password expired or wrong address"],
    connect_failed: ["连不上服务器（网络 / 代理）", "Could not connect (network / proxy)"],
    mcp_not_configured: ["无 token 且 Claude CLI 没配 Slack MCP", "No token and the Claude CLI has no Slack MCP"],
    vault_missing: ["笔记库目录不存在——检查上方「笔记库 raw 目录」", "Vault folder missing — check the raw folder above"],
    vault_empty: ["目录在但没有 .md 笔记", "Folder exists but holds no .md notes"],
    no_api_key: ["提取失败且没有可用的 Anthropic key", "Extraction failed and no Anthropic key is available"],
    extract_failed: ["对至少一条笔记的提取失败", "Extraction failed for at least one note"],
    error: ["出错（详见诊断页）", "Error (see Diagnostics)"],
  };
  const hit = table[reason];
  return hit ? text(hit[0], hit[1]) : reason;
}

function HealthLine({ source, health }: { source: string; health: RadarSourceHealth | undefined }) {
  const { text } = useI18n();
  if (!health) return null;
  const tone = !health.enabled ? "" : health.skip_reason ? " is-warning" : health.stale ? " is-danger" : " is-ok";
  return (
    <li className={`settings-health-line${tone}`} data-source={source}>
      <strong>{source}</strong>
      <span>
        {!health.enabled
          ? text("已关（flag × 开关 合取为关）", "Off (flag × switch = off)")
          : health.skip_reason
            ? text("开着但在静默失败：", "On but silently failing: ") + skipReasonLabel(health.skip_reason, text)
            : health.stale
              ? text("开着但很久没成功跑过", "On but has not succeeded for a long time")
              : text("正常", "Healthy")}
      </span>
      {health.last_ok && <RelativeTime iso={health.last_ok} prefix={text("上次成功 ", "last ok ")} />}
    </li>
  );
}

export function SourcesSection() {
  const { text } = useI18n();
  const { board, secrets } = useAppState();
  useEffect(() => {
    if (!secrets) void refreshSecrets();
  }, [secrets]);
  const health = board?.radar_sources;

  return (
    <CatalogSection sectionId="sources">
      {health && (
        <ul className="settings-health" aria-label={text("来源健康", "Source health")}>
          {(["gmail", "slack", "obsidian"] as const).map((src) => <HealthLine key={src} source={src} health={health[src]} />)}
        </ul>
      )}
      <div className="settings-subhead">{text("凭证（存本机 config/secrets/，0600；保存后可验证）", "Credentials (stored locally in config/secrets/, 0600; verify after saving)")}</div>
      <SecretRow
        name="anthropic-api-key.txt"
        links={[{ label: text("控制台", "Console"), href: "https://console.anthropic.com/settings/keys" }]}
        helper={text("雷达提取的兜底 key（管线主路径走 Claude Code 登录，不需要它）。", "Fallback key for radar extraction (the main pipeline uses the Claude Code login and does not need it).")}
      />
      <SecretRow
        name="slack-user-token.txt"
        links={[{ label: text("接入指南", "Setup guide"), href: "https://github.com/Wan-ZL/zelin-ai-assistant/blob/main/docs/SLACK_SETUP.md" }]}
        helper={text("xoxp- user token；验证通过会自动填好你的 Slack user id（身份零手填）。", "xoxp- user token; a passing verify auto-fills your Slack user id.")}
      />
      <SecretRow
        name="gmail-app-password.txt"
        links={[{ label: text("生成应用专用密码", "Create an app password"), href: "https://myaccount.google.com/apppasswords" }]}
        helper={text("16 位应用专用密码；验证 = IMAP 只读登录一次（需先填上方地址）。", "16-char app password; verify = one read-only IMAP login (fill in the address above first).")}
      />
    </CatalogSection>
  );
}
