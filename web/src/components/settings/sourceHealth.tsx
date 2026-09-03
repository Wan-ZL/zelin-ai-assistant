// 来源健康一行（§48 / §68）：board.radar_sources 的投影（enabled / last_ok / skip_reason / stale——
// 「生效 = flag × 开关」，原生 DiagnosticsRules.effectiveSourceEnabled 读的同一份）。
// 三个接入区（SlackSection / GmailSection / ObsidianSection）与「录制与数据接入」页共用。
// 原生 SettingsSlack / SettingsGmail 的「运行状态（真实轮询结果）」一句也从这里算（runStatusLine）。
import { useI18n } from "../../i18n";
import { RelativeTime } from "../board/cardChrome";
import type { RadarSourceHealth } from "../../types";

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

export function HealthLine({ source, health }: { source: string; health: RadarSourceHealth | undefined }) {
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

/** 原生「运行状态（真实轮询结果）」：运行正常 ✓ 最近成功 <相对时间> / 具体死因 / 状态未知 */
export function RunStatusLine({ health }: { health: RadarSourceHealth | undefined }) {
  const { text } = useI18n();
  if (!health) return <p className="settings-helper">{text("状态未知", "unknown")}</p>;
  if (health.enabled && !health.skip_reason && health.last_ok) {
    return (
      <p className="settings-helper is-ok">
        <RelativeTime iso={health.last_ok} prefix={text("运行正常 ✓ 最近成功 ", "Working ✓ last success ")} />
      </p>
    );
  }
  if (health.skip_reason) return <p className="settings-warning">{skipReasonLabel(health.skip_reason, text)}</p>;
  return <p className="settings-helper">{health.enabled ? text("状态未知", "unknown") : text("已关（flag × 开关 合取为关）", "Off (flag × switch = off)")}</p>;
}
