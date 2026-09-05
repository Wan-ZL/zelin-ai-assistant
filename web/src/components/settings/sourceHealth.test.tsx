// 来源健康一行 + 「运行状态（真实轮询结果）」（CONTRACT §48 / §48.7 追记；原生 SettingsGmail / SettingsSlack 的
// healthSummary + humanSkip）：1) skip_reason 闭集词表（act/lib/radar_health.SKIP_REASON_CODES）每个码都有一句人话、
// 未知码原样——尤其 disabled / command_failed / command_bad_output（§14bis）/ mcp_failed 四句逐字镜像原生；
// 2) RunStatusLine 五态：运行正常 ✓ / 死因（最近一轮 …）/ 最近一轮 … / 还没有运行记录（开着且条目全空，
// N 读 interval_s、没问到就省数字）/ 状态未知（只在投影里没有这一源时）；3) HealthLine 的静默失败句也走同一词表。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LanguageContext } from "../../i18n";
import type { RadarSourceHealth } from "../../types";
import { HealthLine, RunStatusLine, noRunsYetLabel, skipReasonLabel } from "./sourceHealth";

const en = (_zh: string, english: string) => english;
const zh = (chinese: string) => chinese;

/** act/lib/radar_health.SKIP_REASON_CODES（+ public_skip_reason 的折叠码 error）——加码 = 同步这里 */
const SKIP_REASON_CODES = [
  "disabled", "no_credentials", "no_address", "auth_failed", "connect_failed", "command_failed", "command_bad_output",
  "mcp_not_configured", "mcp_failed", "vault_missing", "vault_empty", "no_api_key", "extract_failed", "error",
];

function health(over: Partial<RadarSourceHealth> = {}): RadarSourceHealth {
  return { enabled: true, last_ok: null, skip_reason: null, stale: false, last_attempt: null, test_round: null, ...over };
}

function renderLine(h: RadarSourceHealth | undefined, language: "zh" | "en" = "en", intervalS?: number | null) {
  return render(
    <LanguageContext.Provider value={language}>
      <RunStatusLine health={h} intervalS={intervalS} />
    </LanguageContext.Provider>,
  );
}

afterEach(cleanup);

describe("skipReasonLabel", () => {
  it("has a plain-language sentence for every code in the closed vocabulary, in both languages", () => {
    for (const code of SKIP_REASON_CODES) {
      expect(skipReasonLabel(code, en), code).not.toBe(code);
      expect(skipReasonLabel(code, zh), code).not.toBe(code);
      expect(skipReasonLabel(code, en), code).not.toBe(skipReasonLabel(code, zh));
    }
  });

  it("mirrors the native humanSkip sentences for the four codes the port had left raw", () => {
    expect(skipReasonLabel("disabled", zh)).toBe("上一轮运行时开关还没打开——点「立即测试一轮」再看");
    expect(skipReasonLabel("disabled", en)).toBe("The toggle was still off during the last round — click \"Test one round now\"");
    expect(skipReasonLabel("command_failed", zh)).toBe("抓取命令没跑成（fetch_command 报错/超时）——在终端手动跑一次它看报错");
    expect(skipReasonLabel("command_failed", en)).toBe("The fetch command failed (error/timeout) — run it by hand in a terminal to see why");
    expect(skipReasonLabel("command_bad_output", zh)).toBe("抓取命令的输出不是 JSON 数组——检查 fetch_command 的输出格式");
    expect(skipReasonLabel("command_bad_output", en)).toBe("The fetch command didn't print a JSON array — check its output format");
    expect(skipReasonLabel("mcp_failed", zh)).toBe("MCP 兜底扫描失败（token 批下来后自动改走正式通道）");
    expect(skipReasonLabel("mcp_failed", en)).toBe("The MCP fallback scan failed (the native path takes over once a token is saved)");
  });

  it("passes unknown codes through verbatim (wire add-only: a new code never breaks the render)", () => {
    expect(skipReasonLabel("some_future_code", en)).toBe("some_future_code");
    expect(skipReasonLabel("some_future_code", zh)).toBe("some_future_code");
  });
});

describe("noRunsYetLabel", () => {
  it("puts the launchd interval in minutes into the sentence and drops the number when unknown", () => {
    expect(noRunsYetLabel(300, zh)).toBe("还没有运行记录。等一轮（≤5 分钟）或点「立即测试一轮」。");
    expect(noRunsYetLabel(300, en)).toBe("No runs recorded yet. Wait one round (≤5 min) or click \"Test one round now\".");
    expect(noRunsYetLabel(180, zh)).toBe("还没有运行记录。等一轮（≤3 分钟）或点「立即测试一轮」。");
    expect(noRunsYetLabel(180, en)).toBe("No runs recorded yet. Wait one round (≤3 min) or click \"Test one round now\".");
    expect(noRunsYetLabel(null, zh)).toBe("还没有运行记录。等一轮或点「立即测试一轮」。");
    expect(noRunsYetLabel(undefined, en)).toBe("No runs recorded yet. Wait one round or click \"Test one round now\".");
    expect(noRunsYetLabel(0, en)).toBe("No runs recorded yet. Wait one round or click \"Test one round now\".");
  });
});

describe("<RunStatusLine />", () => {
  it("says unknown only when the projection has no entry for the source at all", () => {
    renderLine(undefined);
    expect(screen.getByText("unknown")).toBeTruthy();
    expect(screen.queryByText(/No runs recorded yet/)).toBeNull();
  });

  it("an enabled source with a blank entry reads as no runs yet, with N from the interval", () => {
    renderLine(health(), "en", 300);
    expect(screen.getByText("No runs recorded yet. Wait one round (≤5 min) or click \"Test one round now\".")).toBeTruthy();
    expect(screen.queryByText("unknown")).toBeNull();
    cleanup();
    renderLine(health(), "zh", 180);
    expect(screen.getByText("还没有运行记录。等一轮（≤3 分钟）或点「立即测试一轮」。")).toBeTruthy();
    cleanup();
    renderLine(health(), "en");
    expect(screen.getByText("No runs recorded yet. Wait one round or click \"Test one round now\".")).toBeTruthy();
  });

  it("an old server without last_attempt but with nothing else to say also reads as no runs yet", () => {
    const legacy = { enabled: true, last_ok: null, skip_reason: null, stale: false } as RadarSourceHealth;
    renderLine(legacy, "en", 300);
    expect(screen.getByText(/No runs recorded yet/)).toBeTruthy();
  });

  it("the no-runs sentence is secondary, not a warning", () => {
    renderLine(health(), "en", 300);
    const p = screen.getByText(/No runs recorded yet/);
    expect(p.className).toBe("settings-helper");
  });

  it("a last attempt without success or a skip reason still reads as last round …", () => {
    renderLine(health({ last_attempt: "2026-09-03T11:57:00Z" }), "en", 300);
    expect(screen.getByText(/last round/).textContent).toMatch(/^last round /);
    expect(screen.queryByText(/No runs recorded yet/)).toBeNull();
  });

  it("a skip reason speaks the vocabulary sentence with the last round in brackets", () => {
    renderLine(health({ skip_reason: "command_failed", last_attempt: "2026-09-03T11:57:00Z" }), "en", 300);
    const line = screen.getByText(/The fetch command failed/);
    expect(line.className).toBe("settings-warning");
    expect(line.textContent).toMatch(/^The fetch command failed .* \(last round .*\)$/);
    cleanup();
    renderLine(health({ skip_reason: "mcp_failed" }), "zh", 180);
    expect(screen.getByText("MCP 兜底扫描失败（token 批下来后自动改走正式通道）")).toBeTruthy();
  });

  it("success and off keep their sentences", () => {
    renderLine(health({ last_ok: "2026-09-03T11:55:00Z", last_attempt: "2026-09-03T11:55:00Z" }), "en", 300);
    expect(screen.getByText(/Working ✓ last success/)).toBeTruthy();
    cleanup();
    renderLine(health({ enabled: false }), "en", 300);
    expect(screen.getByText("Off (flag × switch = off)")).toBeTruthy();
    expect(screen.queryByText(/No runs recorded yet/)).toBeNull();
  });
});

describe("<HealthLine />", () => {
  it("uses the same vocabulary for the silently-failing sentence", () => {
    render(
      <LanguageContext.Provider value="en">
        <ul><HealthLine source="gmail" health={health({ skip_reason: "command_bad_output" })} /></ul>
      </LanguageContext.Provider>,
    );
    expect(screen.getByText(/On but silently failing: The fetch command didn't print a JSON array/)).toBeTruthy();
  });
});
