// 来源健康一行（§48 / §68）：board.radar_sources 的投影（enabled / last_ok / skip_reason / stale——
// 「生效 = flag × 开关」，原生 DiagnosticsRules.effectiveSourceEnabled 读的同一份）。
// 三个接入区（SlackSection / GmailSection / ObsidianSection）与「录制与数据接入」页共用。
// 原生 SettingsSlack / SettingsGmail 的「运行状态（真实轮询结果）」一句也从这里算（RunStatusLine；
// 后台雷达行与 立即测试一轮 在 RadarAgentPanel，§48.7）。
import { useI18n } from "../../i18n";
import { RelativeTime } from "../board/cardChrome";
import type { RadarSourceHealth } from "../../types";

type Text = (zh: string, en: string) => string;

/** §48 skip_reason 闭集词表（act/lib/radar_health.SKIP_REASON_CODES 全员 + §48.4 折叠码 error）→ 一句人话（未知原样）。
 *  disabled / command_failed / command_bad_output / mcp_failed 四句逐字镜像原生 SettingsGmail / SettingsSlack humanSkip
 *  （§14bis 要求 command 类码在设置页说成大白话；mcp_failed 出机前已被 §48.4 去尾，原生尾随的错误摘录不在投影里）。
 *  「全员」由 tests/test_skip_reason_vocabulary_mirror.py 读本文件钉死——一行一码 `code: ["zh", "en"],` 的格式是它的解析契约。 */
export function skipReasonLabel(reason: string, text: Text): string {
  const table: Record<string, [string, string]> = {
    disabled: ["上一轮运行时开关还没打开——点「立即测试一轮」再看", "The toggle was still off during the last round — click \"Test one round now\""],
    no_credentials: ["没有凭证——在下方粘贴并保存", "No credential — paste and save it below"],
    no_address: ["没有邮箱地址——填上方「Gmail 地址」", "No address — fill in Gmail address above"],
    auth_failed: ["登录被拒——应用专用密码过期或地址不对", "Login rejected — app password expired or wrong address"],
    connect_failed: ["连不上服务器（网络 / 代理）", "Could not connect (network / proxy)"],
    command_failed: ["抓取命令没跑成（fetch_command 报错/超时）——在终端手动跑一次它看报错", "The fetch command failed (error/timeout) — run it by hand in a terminal to see why"],
    command_bad_output: ["抓取命令的输出不是 JSON 数组——检查 fetch_command 的输出格式", "The fetch command didn't print a JSON array — check its output format"],
    mcp_not_configured: ["无 token 且 Claude CLI 没配 Slack MCP", "No token and the Claude CLI has no Slack MCP"],
    mcp_failed: ["MCP 兜底扫描失败（token 批下来后自动改走正式通道）", "The MCP fallback scan failed (the native path takes over once a token is saved)"],
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

/** 原生 healthSummary 的 `guard healthHasData`：开着、但 health 条目一片空白（没成功过、没死因、连一轮尝试都没落笔）
 *  = 雷达还没跑过——说「等一轮或点按钮」而不是「状态未知」。N = launchd 模板的 StartInterval（分钟，同 agentStatusLabel
 *  的 N；原生 Gmail 写死 ≤5 / Slack ≤3）；还没问到 launchd 时省掉括号里的数字。 */
export function noRunsYetLabel(intervalS: number | null | undefined, text: Text): string {
  const minutes = intervalS ? Math.round(intervalS / 60) : null;
  return minutes
    ? text(`还没有运行记录。等一轮（≤${minutes} 分钟）或点「立即测试一轮」。`, `No runs recorded yet. Wait one round (≤${minutes} min) or click "Test one round now".`)
    : text("还没有运行记录。等一轮或点「立即测试一轮」。", "No runs recorded yet. Wait one round or click \"Test one round now\".");
}

/** 原生「运行状态（真实轮询结果）」（healthSummary）：运行正常 ✓ 最近成功 <相对时间> / 具体死因（最近一轮 X）/
 *  最近一轮 X / 还没有运行记录（开着且条目全空，noRunsYetLabel）/ 状态未知（只在投影里根本没有这一源时）；
 *  `last_attempt` 是 §48.7 add-only 投影（老 server 缺席 → 「还没有运行记录」——它同样没有 last_ok / skip_reason 可说）。 */
export function RunStatusLine({ health, intervalS }: { health: RadarSourceHealth | undefined; intervalS?: number | null }) {
  const { text } = useI18n();
  if (!health) return <p className="settings-helper">{text("状态未知", "unknown")}</p>;
  if (health.enabled && !health.skip_reason && health.last_ok) {
    return (
      <p className="settings-helper is-ok">
        <RelativeTime iso={health.last_ok} prefix={text("运行正常 ✓ 最近成功 ", "Working ✓ last success ")} />
      </p>
    );
  }
  const attempt = health.last_attempt ? <RelativeTime iso={health.last_attempt} prefix={text("最近一轮 ", "last round ")} /> : null;
  if (health.skip_reason) {
    return (
      <p className="settings-warning">
        {skipReasonLabel(health.skip_reason, text)}
        {attempt && <>{text("（", " (")}{attempt}{text("）", ")")}</>}
      </p>
    );
  }
  if (!health.enabled) return <p className="settings-helper">{text("已关（flag × 开关 合取为关）", "Off (flag × switch = off)")}</p>;
  if (!attempt) return <p className="settings-helper">{noRunsYetLabel(intervalS, text)}</p>;
  return <p className="settings-helper is-warning">{attempt}</p>;
}
