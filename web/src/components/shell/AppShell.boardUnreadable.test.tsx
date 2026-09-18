// AppShell / ErrorBanner 对「dashboard.json 在、但 server 读不动」的分派（CONTRACT §49 追记 2026-09-18，issue #423）：
//   - 从未有过快照：整页第四态 = 那一行（含 errno）+ 「重试」，**绝不复用 BoardMissingState** —— 它给的「立即生成一次」
//     会 POST /api/setup/seed-dashboard 拿一份可能零卡的看板原子替换掉最后一份好快照，在一台只是读不动的机器上那是数据丢失；
//   - 有旧快照：页面照常渲染 + warning 横幅是那一行，且**不承诺自动重连**（503 是一次真回答，api.request 不重试它）；
//   - 恰好一条 alert：PipelineBanner 刻意不长 dashboard_error 分支，就是为了这句话不出现两遍（§47.4 读者 3）；
//   - 四个 BOARD_FED_PAGES 同此一态：读不动时把回收站渲染成空列表是同一句谎话。
// api 层 mock 掉，经 store 真实 action 驱动；零真实网络。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchBoard, fetchHealth } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshBoard, refreshHealth, resetStoreForTests, setLanguage } from "../../store";
import type { Board, HealthSnapshot } from "../../types";
import { AppShell } from "./AppShell";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchBoard: vi.fn(), fetchCard: vi.fn(), fetchHealth: vi.fn(), postAction: vi.fn(), postSeedDashboard: vi.fn() };
});

const fetchBoardMock = vi.mocked(fetchBoard);
const fetchHealthMock = vi.mocked(fetchHealth);

// store 从 `details` 的 errno / strerror 拼这一句——**不是**照搬 server 那句「see details.errno」
// （照搬会让页面指着一个屏幕上根本没有的数字，本轮 review 抓到的原状）
const UNREADABLE_LINE = "The board file can't be read: errno 1: Operation not permitted";

function makeBoard(): Board {
  return { generated_at: "2026-09-18T10:00:00Z", counts: {}, needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [] };
}

/** server 的 503 原形（errno 在 details 里，消息刻意不断言文件存在） */
const unreadable = () => new ApiError(503, {
  error: {
    code: "BOARD_UNREADABLE",
    message: "cannot read dashboard.json — see details.errno",
    details: { path: "/Volumes/Storage/.../state/dashboard.json", errno: 1, strerror: "Operation not permitted" },
  },
});

/** 读被拒时 actd 常常还活着（owner 那台机器上 dashboard.json 每 5 s 还在被重写）→ verdict 仍是 ok */
const okHealthWithDenial: HealthSnapshot = {
  verdict: "ok",
  heartbeat: { age_s: 3, phase: "idle", pid: 42, interval: 10, stale_after_s: 90, stale: false },
  dashboard: null,
  dashboard_error: { path: "/Volumes/Storage/.../state/dashboard.json", errno: 1, strerror: "Operation not permitted" },
  loop_health: { consecutive_failures: 0, last_error: null },
  checked_at: "2026-09-18T10:00:05Z",
};

function renderShell(language: "zh" | "en" = "en") {
  return render(
    <LanguageContext.Provider value={language}>
      <AppShell>
        <div>page-content</div>
      </AppShell>
    </LanguageContext.Provider>,
  );
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  resetStoreForTests();
  setLanguage("en");
  fetchBoardMock.mockReset();
  fetchHealthMock.mockReset();
  fetchHealthMock.mockResolvedValue(okHealthWithDenial);
});

afterEach(() => {
  cleanup();
});

describe("AppShell · 看板文件读不动", () => {
  it("从未有过快照：整页说读不动 + 重试，而不是「还没写出数据」", async () => {
    fetchBoardMock.mockRejectedValue(unreadable());
    await refreshBoard();
    renderShell();

    expect(screen.getByText(UNREADABLE_LINE)).toBeTruthy();
    // 不借「文件不在」的空态说话
    expect(screen.queryByText("The background service hasn't produced data yet")).toBeNull();
    // 也不借离线文案（server 刚答过话）
    expect(screen.queryByText("Can't reach the local server")).toBeNull();
    expect(screen.queryByText("page-content")).toBeNull();
  });

  it("那一态里没有「立即生成一次」——播种会覆盖掉最后一份好快照", async () => {
    fetchBoardMock.mockRejectedValue(unreadable());
    await refreshBoard();
    renderShell();

    expect(screen.queryByRole("button", { name: "Generate now" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Start service" })).toBeNull();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("「重试」走 refreshBoard，读得动之后空态退场", async () => {
    fetchBoardMock.mockRejectedValueOnce(unreadable());
    await refreshBoard();
    renderShell();

    fetchBoardMock.mockResolvedValueOnce(makeBoard());
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("page-content");
    expect(screen.queryByText(UNREADABLE_LINE)).toBeNull();
  });

  it("有旧快照：页面照常渲染 + warning 横幅那一行，且不承诺自动重连", async () => {
    fetchBoardMock.mockResolvedValueOnce(makeBoard());
    await refreshBoard();
    fetchBoardMock.mockRejectedValueOnce(unreadable());
    await refreshBoard();
    renderShell();

    expect(screen.getByText("page-content")).toBeTruthy();   // 上一版看板仍是真话
    expect(screen.getByText(UNREADABLE_LINE)).toBeTruthy();
    expect(screen.getByText(/could not read the board file/)).toBeTruthy();
    expect(screen.queryByText(/Reconnecting automatically/)).toBeNull();
    // 有快照时本横幅没有按钮——别叫人「点重试」（review 抓到的原状）
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("the errno itself is on screen — the whole point of the issue", async () => {
    fetchBoardMock.mockRejectedValue(unreadable());
    await refreshBoard();
    renderShell();
    // 不是「see details.errno」那种指路，而是那个数字本身
    expect(screen.getByText(/errno 1/)).toBeTruthy();
    expect(screen.getByText(/Operation not permitted/)).toBeTruthy();
    expect(screen.queryByText(/see details\.errno/)).toBeNull();
  });

  it("a denial whose details carry no errno degrades to the server sentence, never 'errno null'", async () => {
    // 裸 OSError（拿不到号码）——server 仍诚实答 503 带 errno:null，客户端不许编一个数字
    fetchBoardMock.mockRejectedValue(new ApiError(503, {
      error: { code: "BOARD_UNREADABLE", message: "cannot read dashboard.json — see details.errno",
               details: { path: "/x", errno: null, strerror: null } },
    }));
    await refreshBoard();
    renderShell();
    expect(screen.queryByText(/errno null/)).toBeNull();
    expect(screen.getByText(/The board file can't be read/)).toBeTruthy();
  });

  it("neither surface claims the file exists — the server deliberately refused to assert that", async () => {
    // ELOOP / ENOTDIR / ENAMETOOLONG 都落在 503 这一臂，那几路谁也没查过文件在不在
    fetchBoardMock.mockRejectedValue(unreadable());
    await refreshBoard();
    renderShell();
    expect(screen.queryByText(/The file is there/)).toBeNull();
  });

  it("the health banner stays quiet: its 'Start service' remedy is wrong for a permission problem", async () => {
    // state/ 整个读不动时心跳也 stat 不到 → verdict stale，横幅会说「后台服务没在运行」+ 给一颗
    // 「启动后台服务」——对一个权限问题那是错的修法，而看板面已经带着 errno 在说真话了
    fetchHealthMock.mockResolvedValue({
      ...okHealthWithDenial,
      verdict: "stale",
      heartbeat: null,
    });
    await refreshHealth();
    fetchBoardMock.mockRejectedValue(unreadable());
    await refreshBoard();
    renderShell();
    expect(screen.queryByText("Background service is not running")).toBeNull();
    expect(screen.queryByRole("button", { name: "Start service" })).toBeNull();
    expect(screen.queryAllByRole("alert")).toHaveLength(0);   // 整页态自己说话，没有横幅
    expect(screen.getByText(UNREADABLE_LINE)).toBeTruthy();
  });

  it("恰好一条 alert（同一句话不出现两遍）——健康快照带 dashboard_error 时也一样", async () => {
    fetchBoardMock.mockResolvedValueOnce(makeBoard());
    await refreshBoard();
    await refreshHealth();                                   // verdict ok + dashboard_error
    fetchBoardMock.mockRejectedValueOnce(unreadable());
    await refreshBoard();
    renderShell();

    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  it("zh 文案逐字", async () => {
    setLanguage("zh");
    fetchBoardMock.mockRejectedValue(unreadable());
    await refreshBoard();
    renderShell("zh");
    expect(screen.getByText(/看板文件读不出来: /)).toBeTruthy();
  });

  it("回收站这类读同一份快照的页也走这一态，不渲染成空列表", async () => {
    window.history.replaceState(null, "", "/?page=trash");
    fetchBoardMock.mockRejectedValue(unreadable());
    await refreshBoard();
    renderShell();
    expect(screen.getByText(UNREADABLE_LINE)).toBeTruthy();
    expect(screen.queryByText("page-content")).toBeNull();
  });

  it("自拉快照的页（设置）不受影响", async () => {
    window.history.replaceState(null, "", "/?page=settings");
    fetchBoardMock.mockRejectedValue(unreadable());
    await refreshBoard();
    renderShell();
    expect(screen.getByText("page-content")).toBeTruthy();
  });
});
