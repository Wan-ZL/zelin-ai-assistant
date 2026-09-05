// 设置页「导入 Claude Code 工作」（CONTRACT §68.10 追记；原生 SettingsClaudeImport locallyImported + 计数行）：
//   1) 扫描结果上方一行「找到 N 个会话，其中 M 个在等你回复（已默认勾选）」，M 只数 等你回复 ∧ 未回答；
//   2) 「导入所选」之后：已提交的行立刻从列表消失、回执句两条去向（提案 / 潜在任务）、不再出「没有可导入」空态；
//   3) 重新扫描回来同一批 id → 已导入的仍不列出、不预勾、不计数——只剩没导过的；卸载重挂（看板 ↔ 设置）亦然；
//   4) 导入 payload 只带 candidates ∩ picked 的 id（藏起来的已导入行永不重复提交）；
//   5) 导入失败 → 行留在原处、可再点。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchClaudeSessions, postAction } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { ClaudeSessionCandidate, ClaudeSessionsScan } from "../../types";
import { ClaudeImportSection } from "./ClaudeImportSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchClaudeSessions: vi.fn(), postAction: vi.fn() };
});

function candidate(id: string, over: Partial<ClaudeSessionCandidate> = {}): ClaudeSessionCandidate {
  return { session_id: id, title: `Session ${id}`, project: "repo", last_activity: "2026-09-05T10:00:00Z", ...over };
}

const WAITING_A = candidate("aaa", { ended_waiting_on_user: true });
const WAITING_B = candidate("bbb", { ended_waiting_on_user: true });
const ANSWERED = candidate("ccc", { ended_waiting_on_user: true, answered: true });
const RECENT = candidate("ddd");

function scan(candidates: ClaudeSessionCandidate[]): ClaudeSessionsScan {
  return { ok: true, window: 7, candidates };
}

function renderSection() {
  return render(
    <LanguageContext.Provider value="en">
      <ClaudeImportSection />
    </LanguageContext.Provider>,
  );
}

function rowTitles(): string[] {
  const list = screen.queryByRole("list", { name: "Session candidates" });
  return list ? within(list).getAllByRole("listitem").map((li) => li.querySelector(".settings-list-title")?.textContent ?? "") : [];
}

beforeEach(() => {
  resetStoreForTests();   // 也清 claudeSessionsImported（与扫描快照同寿命）
  vi.mocked(fetchClaudeSessions).mockReset().mockResolvedValue(scan([WAITING_A, WAITING_B, ANSWERED, RECENT]));
  vi.mocked(postAction).mockReset().mockResolvedValue({ ok: true });
});

afterEach(() => {
  cleanup();
});

describe("ClaudeImportSection", () => {
  it("shows the found / waiting count line; waiting counts only unanswered waiting-on-you rows", async () => {
    renderSection();
    expect((await screen.findByText("Found 4 sessions — 2 waiting on you (pre-checked)")).tagName).toBe("P");
    expect(rowTitles()).toEqual(["Session aaa", "Session bbb", "Session ccc", "Session ddd"]);
    expect(screen.getByRole("button", { name: "Import selected (2)" })).toBeTruthy();
  });

  it("drops the imported rows right away and states both landing lanes", async () => {
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "Import selected (2)" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({ action: "import_claude_sessions", session_ids: ["aaa", "bbb"] }));
    await waitFor(() => expect(rowTitles()).toEqual(["Session ccc", "Session ddd"]));
    expect(screen.getByText("Found 2 sessions — 0 waiting on you (pre-checked)")).toBeTruthy();
    expect((screen.getByRole("status")).textContent).toBe(
      "Submitted 2 — the background service turns them into board cards within seconds (waiting-on-you ones go to Proposals, the rest to Backlog).",
    );
    expect((screen.getByRole("button", { name: "Import selected (0)" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("keeps imported ids out of a re-scan that returns the same batch", async () => {
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "Import selected (2)" }));
    await waitFor(() => expect(rowTitles()).toEqual(["Session ccc", "Session ddd"]));
    // actd 还没处理 inbox 动作：扫描器把同一批原样吐回来，外加一个新的「等你回复」
    const NEW_WAITING = candidate("eee", { ended_waiting_on_user: true });
    vi.mocked(fetchClaudeSessions).mockResolvedValue(scan([WAITING_A, WAITING_B, ANSWERED, RECENT, NEW_WAITING]));
    fireEvent.click(screen.getByRole("button", { name: "Re-scan" }));
    expect(screen.queryByRole("status")).toBeNull();   // 回执句随重新扫描清掉（原生 scan()）
    await waitFor(() => expect(rowTitles()).toEqual(["Session ccc", "Session ddd", "Session eee"]));
    expect(screen.getByText("Found 3 sessions — 1 waiting on you (pre-checked)")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Import selected (1)" })).toBeTruthy();   // 只预勾新来的那条
    fireEvent.click(screen.getByRole("button", { name: "Import selected (1)" }));
    await waitFor(() => expect(postAction).toHaveBeenLastCalledWith({ action: "import_claude_sessions", session_ids: ["eee"] }));
  });

  it("a re-scan that brings only imported ids shows the empty state, and 全选 never re-submits them", async () => {
    vi.mocked(fetchClaudeSessions).mockResolvedValue(scan([WAITING_A, RECENT]));
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "Select all" }));
    fireEvent.click(screen.getByRole("button", { name: "Import selected (2)" }));
    await waitFor(() => expect(postAction).toHaveBeenCalledWith({ action: "import_claude_sessions", session_ids: ["aaa", "ddd"] }));
    await waitFor(() => expect(rowTitles()).toEqual([]));
    expect(screen.queryByText("No importable sessions in this window.")).toBeNull();   // 刚导入完只留回执句
    fireEvent.click(screen.getByRole("button", { name: "Re-scan" }));
    await waitFor(() => expect(fetchClaudeSessions).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("No importable sessions in this window.")).toBeTruthy();
    expect(screen.queryByText(/^Found /)).toBeNull();
    expect(postAction).toHaveBeenCalledTimes(1);
  });

  it("imported ids stay hidden across unmount → remount while the store keeps the cached scan", async () => {
    // 看板 ↔ 设置 来回：SettingsPage 卸载重挂，store 里的扫描快照还是导入前那份（不重新 GET）
    const first = renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "Import selected (2)" }));
    await waitFor(() => expect(rowTitles()).toEqual(["Session ccc", "Session ddd"]));
    first.unmount();
    renderSection();
    await waitFor(() => expect(rowTitles()).toEqual(["Session ccc", "Session ddd"]));
    expect(fetchClaudeSessions).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Found 2 sessions — 0 waiting on you (pre-checked)")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Import selected (0)" }) as HTMLButtonElement).disabled).toBe(true);
    // 「全选」也只勾可见行——藏起来的 aaa/bbb 永不重复提交
    fireEvent.click(screen.getByRole("button", { name: "Select all" }));
    fireEvent.click(screen.getByRole("button", { name: "Import selected (2)" }));
    await waitFor(() => expect(postAction).toHaveBeenLastCalledWith({ action: "import_claude_sessions", session_ids: ["ccc", "ddd"] }));
  });

  it("a rejected import leaves the rows in place so the user can retry", async () => {
    vi.mocked(postAction).mockRejectedValue(new ApiError(400, { error: { code: "INVALID_FIELD", message: "session_ids: bad id" } }));
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "Import selected (2)" }));
    expect((await screen.findByRole("alert")).textContent).toBe("session_ids: bad id");
    expect(rowTitles()).toEqual(["Session aaa", "Session bbb", "Session ccc", "Session ddd"]);
    expect((screen.getByRole("button", { name: "Import selected (2)" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
