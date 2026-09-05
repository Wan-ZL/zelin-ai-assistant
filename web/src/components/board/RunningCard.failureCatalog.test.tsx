// §25 卡片错误行的人话 + 对症一键（原生 Cards.swift:1646-1653 / 1667-1711 TaskRow.errorLine；parity 批次
// review-running-card-fixes，gap board-cards-error-failure-catalog）：
//   1) last_error_id 在 store.failures（GET /api/failures）里 → 卡面 = 前缀 + 当前语言那句，原文降到 title 气泡；
//   2) 目录里没有这个 id / 没 id / 目录还没回 → 原文照旧；
//   3) 动作行：FailureActionButton（原生 actionLabel 逐字，settings/failureAction.tsx 唯一实现）排在 让 AI 修 之前；
//      未知 id 不装按钮，让 AI 修 照旧；
//   4) 排队卡看 dispatch_error_id（前缀「派发失败：」）；刹车行（blocked + last_error_id）也给对症按钮；
//   5) 切语言换句子（server-owned 双语，不是第二套 i18n）。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext, type Language } from "../../i18n";
import { refreshFailures, resetStoreForTests } from "../../store";
import type { FailureCatalog, TaskRow } from "../../types";
import { failureSentence, RunningCard } from "./RunningCard";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  fetchFailures: vi.fn(),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
}));
import { fetchFailures } from "../../api";

const RAW = "error: An unknown error occurred, possibly due to low max file descriptors";
const catalog: FailureCatalog = { failures: {
  claude_blind: {
    zh: "后台起的 claude 读不到任务目录——给它「完全磁盘访问」",
    en: "The claude launched in the background cannot read the task folder — grant it Full Disk Access",
    action_id: "open_deps",
  },
  claude_cli_missing: { zh: "没装 Claude Code", en: "Claude Code is not installed", action_id: "install_claude" },
  network_error: { zh: "网络不通", en: "Network unreachable", action_id: "retry" },
} };

const renderIn = (language: Language, node: React.ReactNode) =>
  render(<LanguageContext.Provider value={language}>{node}</LanguageContext.Provider>);

function workingRow(extra: Partial<TaskRow> = {}): TaskRow {
  return { id: "R-105", name: "修 flaky e2e", state: "working", ...extra };
}

beforeEach(async () => {
  resetStoreForTests();
  vi.mocked(fetchFailures).mockReset().mockResolvedValue(catalog);
  await refreshFailures();
});
afterEach(cleanup);

describe("failureSentence（原生 FailureCatalog.message）", () => {
  it("目录里的 id → 当前语言那句；没 id / 目录没有 / 目录还没回 / 空句 → null", () => {
    expect(failureSentence("claude_blind", catalog, "en")).toBe(catalog.failures.claude_blind.en);
    expect(failureSentence("claude_blind", catalog, "zh")).toBe(catalog.failures.claude_blind.zh);
    expect(failureSentence(null, catalog, "en")).toBeNull();
    expect(failureSentence("", catalog, "en")).toBeNull();
    expect(failureSentence("not_a_known_id", catalog, "en")).toBeNull();
    expect(failureSentence("claude_blind", null, "en")).toBeNull();
    expect(failureSentence("x", { failures: { x: { zh: "", en: "" } } }, "en")).toBeNull();
  });
});

describe("working card with a classified last_error_id", () => {
  it("卡面说人话（前缀 + 目录句），原文只在 title 气泡；对症按钮排在 让 AI 修 之前", () => {
    renderIn("en", <RunningCard row={workingRow({ last_error: RAW, last_error_id: "claude_blind" })} />);
    const line = document.querySelector(".card-error-line") as HTMLElement;
    expect(line.textContent).toBe(`Error: ${catalog.failures.claude_blind.en}`);
    expect(line.getAttribute("title")).toBe(RAW);
    expect(screen.queryByText(RAW)).toBeNull();
    // 按钮行：去诊断（claude_blind 的 actionLabel，深链依赖检查区）在 让 AI 修 之前
    const actions = document.querySelector(".card-actions") as HTMLElement;
    const labels = Array.from(actions.querySelectorAll("a.btn, button.btn")).map((el) => el.textContent);
    const fix = labels.indexOf("Open diagnostics");
    const ai = labels.indexOf("Fix with AI");
    expect(fix).toBeGreaterThanOrEqual(0);
    expect(ai).toBeGreaterThan(fix);
    const link = screen.getByRole("link", { name: "Open diagnostics" });
    expect(link.getAttribute("href")).toContain("page=settings");
    expect(link.getAttribute("href")).toContain("anchor=deps");
  });

  it("切到中文 → 中文那句（server-owned 双语句，不是 web 自己的第二套翻译）", () => {
    renderIn("zh", <RunningCard row={workingRow({ last_error: RAW, last_error_id: "claude_blind" })} />);
    const line = document.querySelector(".card-error-line") as HTMLElement;
    expect(line.textContent).toBe(`错误：${catalog.failures.claude_blind.zh}`);
    expect(screen.getByRole("link", { name: "去诊断" })).toBeTruthy();
  });

  it("目录里没有这个 id → 原文照旧、没有对症按钮、让 AI 修 照旧", () => {
    renderIn("en", <RunningCard row={workingRow({ last_error: "Traceback: boom", last_error_id: "some_future_id" })} />);
    const line = document.querySelector(".card-error-line") as HTMLElement;
    expect(line.textContent).toBe("Error: Traceback: boom");
    expect(screen.getByRole("button", { name: "Fix with AI" })).toBeTruthy();
    const actions = document.querySelector(".card-actions") as HTMLElement;
    expect(actions.querySelectorAll("a.btn")).toHaveLength(0);
  });

  it("未分类（last_error_id 缺席 / null）→ 原文 + 让 AI 修 兜底（§25：绝不硬凑分类）", () => {
    renderIn("en", <RunningCard row={workingRow({ last_error: "Traceback: boom", last_error_id: null })} />);
    expect((document.querySelector(".card-error-line") as HTMLElement).textContent).toBe("Error: Traceback: boom");
    expect(screen.getByRole("button", { name: "Fix with AI" })).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("目录里有 id 但它没有 in-app 动作（network_error）→ 人话在、只有 让 AI 修", () => {
    renderIn("en", <RunningCard row={workingRow({ last_error: "ECONNRESET", last_error_id: "network_error" })} />);
    expect((document.querySelector(".card-error-line") as HTMLElement).textContent).toBe("Error: Network unreachable");
    expect(screen.getByRole("button", { name: "Fix with AI" })).toBeTruthy();
    expect(document.querySelectorAll(".card-actions a.btn")).toHaveLength(0);
  });

  it("目录还没回（store.failures null）→ 原文；按钮标签表是本地的，所以对症按钮照给", () => {
    resetStoreForTests();
    renderIn("en", <RunningCard row={workingRow({ last_error: RAW, last_error_id: "claude_blind" })} />);
    expect((document.querySelector(".card-error-line") as HTMLElement).textContent).toBe(`Error: ${RAW}`);
    expect(screen.getByRole("link", { name: "Open diagnostics" })).toBeTruthy();
  });
});

describe("queued card with a classified dispatch_error_id", () => {
  it("前缀「派发失败：」+ 目录句；安装页 = 外链（claude_cli_missing）", () => {
    renderIn("en", (
      <RunningCard row={{ id: "R-106", name: "排队卡", state: "queued", dispatch_error: "claude: command not found", dispatch_error_id: "claude_cli_missing" }} />
    ));
    const line = document.querySelector(".card-error-line") as HTMLElement;
    expect(line.textContent).toBe("Dispatch failed: Claude Code is not installed");
    expect(line.getAttribute("title")).toBe("claude: command not found");
    const link = screen.getByRole("link", { name: "Install page" });
    expect(link.getAttribute("href")).toBe("https://claude.com/claude-code");
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("排队卡不看 last_error_id（两个字段各归各的行形）", () => {
    renderIn("en", (
      <RunningCard row={{ id: "R-106", name: "排队卡", state: "queued", dispatch_error: "boom", dispatch_error_id: null, last_error_id: "claude_blind" }} />
    ));
    expect((document.querySelector(".card-error-line") as HTMLElement).textContent).toBe("Dispatch failed: boom");
    expect(screen.queryByRole("link", { name: "Open diagnostics" })).toBeNull();
  });
});

describe("dispatch-halted blocked row (§4 storm brake) keeps the fix button", () => {
  it("blocked + last_error_id → 对症按钮 + 让 AI 修；错误行本身不重复（question 已带 §25 句）", () => {
    renderIn("en", (
      <RunningCard
        row={{ id: "R-175", name: "被拦的卡", state: "blocked", dispatch_halted: true, dispatch_attempts: 5, last_error: RAW, last_error_id: "claude_blind", question: "Launch failed 5 times…" }}
        isBlocked
      />
    ));
    expect(document.querySelector(".card-error-line")).toBeNull();
    expect(screen.getByRole("link", { name: "Open diagnostics" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Fix with AI" })).toBeTruthy();
  });
});
