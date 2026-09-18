// AppShell / ErrorBanner 对第四态「读不到 dashboard.json、而且不是因为它不在」的分派
// （CONTRACT §49 + §54.1 追记 2026-09-18，issue #423）：
//   - 没有旧快照 → 整页说真话（带 errno）+ 重试；**不**是「连不上本地服务」（server 刚答过话），
//     **不**是「后台服务还没写出数据」，尤其**不给「立即生成一次」**——那条路会覆盖那个文件，而我们不知道它里面有什么；
//   - 有旧快照 → ErrorBanner 的 warning 变体：旧板照常渲染，横幅一行说清等的是读权限回来；
//   - 健康横幅**不**因 boardError 闭嘴（它仍是 null）；但一个**从读不到的文件推出来的** verdict
//     （`stale` ← 「没有心跳文件」，而 health 明说心跳文件读不了）必须闭嘴——§47.4 追记 2026-09-18；
//   - 非看板页（自拉快照的 ?page=）照常渲染 children。
// api 层 mock 掉，经 store 真实 action 驱动状态；零真实网络 / 子进程。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchBoard, fetchHealth } from "../../api";
import { LanguageContext } from "../../i18n";
import { refreshBoard, refreshHealth, resetStoreForTests, setConnection, setLanguage } from "../../store";
import type { Board, HealthSnapshot } from "../../types";
import { AppShell } from "./AppShell";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchBoard: vi.fn(), fetchCard: vi.fn(), fetchHealth: vi.fn() };
});

const fetchBoardMock = vi.mocked(fetchBoard);
const fetchHealthMock = vi.mocked(fetchHealth);

const OFFLINE_TITLE = "Can't reach the local server";
const MISSING_TITLE = "The background service hasn't produced data yet";
const SERVER_MESSAGE =
  "dashboard.json could not be read by this server process"
  + " (the read failed with an error other than 'no such file')";
const UNREADABLE_TITLE =
  "Can't read dashboard.json: " + SERVER_MESSAGE + " (errno 1 Operation not permitted)";

function makeBoard(): Board {
  return {
    generated_at: new Date().toISOString(),
    counts: {},
    needs_approval: [],
    running: [],
    needs_input: [],
    review: [],
    completed: [],
    debt: [],
    trash: [],
  };
}

/** server/board_source.py 对「读不了、而且不是因为它不在」抛的 envelope 原形（503 BOARD_UNREADABLE） */
const unreadable = () => new ApiError(503, {
  error: {
    code: "BOARD_UNREADABLE",
    message: SERVER_MESSAGE,
    details: { path: "/h/state/dashboard.json", errno: 1, strerror: "Operation not permitted" },
  },
});

/** 心跳文件读得到、却已过期：横幅照说「卡住了」——`unreadable` 只点了 dashboard，
 *  verdict 的依据（心跳的 mtime）没有被这次拒绝碰到，所以那句断言仍然有据。 */
const stalledHealth: HealthSnapshot = {
  verdict: "stalled",
  heartbeat: { age_s: 999, phase: "idle", pid: 1, interval: 10, stale_after_s: 90, stale: true },
  dashboard: null,
  loop_health: { consecutive_failures: 0, last_error: null },
  unreadable: { dashboard: { errno: 1, strerror: "Operation not permitted" } },
  checked_at: "2026-09-18T08:00:05Z",
};

/** 三个 state 文件**全都**读不到（`state/` 整个被拒）：verdict 掉到 `stale`，但它的全部依据
 *  是「没有心跳文件」——而这次答的是「心跳文件读不了」。横幅必须闭嘴（§47.4 追记 2026-09-18）。 */
const staleFromUnreadableHealth: HealthSnapshot = {
  verdict: "stale",
  heartbeat: null,
  dashboard: null,
  loop_health: { consecutive_failures: 0, last_error: null },
  unreadable: {
    heartbeat: { errno: 13, strerror: "Permission denied" },
    dashboard: { errno: 13, strerror: "Permission denied" },
    loop_health: { errno: 13, strerror: "Permission denied" },
  },
  checked_at: "2026-09-18T08:00:05Z",
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

function goTo(search: string) {
  window.history.replaceState(null, "", `/${search}`);
}

describe("AppShell · dashboard.json 读不了（503 BOARD_UNREADABLE）", () => {
  beforeEach(() => {
    resetStoreForTests();
    setLanguage("en");   // store 的语言决定 failBoardUnreadable 那句话（它在 action 里生成，不在渲染里）
    fetchBoardMock.mockReset();
    fetchHealthMock.mockReset();
    goTo("");
  });

  afterEach(() => {
    cleanup();
    goTo("");
  });

  it("无快照 → 带 errno 的实话；既不是离线文案也不是「还没写出数据」", async () => {
    fetchBoardMock.mockRejectedValue(unreadable());
    await refreshBoard();
    renderShell();
    expect(screen.getByText(UNREADABLE_TITLE)).toBeTruthy();
    expect(screen.queryByText(OFFLINE_TITLE)).toBeNull();
    expect(screen.queryByText(MISSING_TITLE)).toBeNull();
    expect(screen.queryByText(/dev-preview\.sh/)).toBeNull();
    expect(screen.queryByText("page-content")).toBeNull();
  });

  it("不给「立即生成一次」——我们不知道那个文件里有什么，重建可能覆盖掉好数据", async () => {
    // health 也拉到（否则 PipelineBanner 直接 early-return，这条断言就是空的）
    fetchBoardMock.mockRejectedValue(unreadable());
    fetchHealthMock.mockResolvedValue(staleFromUnreadableHealth);
    await refreshBoard();
    await refreshHealth();
    renderShell();
    expect(screen.queryByRole("button", { name: "Generate now" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Start service" })).toBeNull();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("verdict=stale 但心跳文件读不到 → 横幅闭嘴：不断言「没在运行」，也不劝人去启动它", async () => {
    // §47.4 追记 2026-09-18：`stale` 的全部依据是「没有心跳文件」，而这次答的是
    // 「心跳文件读不了」——两件事。旧行为里 404 靠 pipelineBannerMuted 压住这条横幅，
    // 新的第四态 boardMissing=false 不再触发那个 mute，所以判据必须落在 health 上。
    fetchBoardMock.mockRejectedValue(unreadable());
    fetchHealthMock.mockResolvedValue(staleFromUnreadableHealth);
    await refreshBoard();
    await refreshHealth();
    renderShell();
    expect(screen.queryByText("Background service is not running")).toBeNull();
    expect(screen.queryByRole("button", { name: "Start service" })).toBeNull();
    expect(screen.getByText(UNREADABLE_TITLE)).toBeTruthy();
  });

  it("中文侧同一句话（文案是内联 text 对，不走第二套双语机制）", async () => {
    setLanguage("zh");
    fetchBoardMock.mockRejectedValue(unreadable());
    await refreshBoard();
    renderShell("zh");
    expect(screen.getByText(/读不到 dashboard\.json/)).toBeTruthy();
    expect(screen.getByText(/这不是「文件不存在」/)).toBeTruthy();
  });

  it("依据没被这次拒绝碰到的 verdict 照常说话——boardError 是 null，闭麦开关没被按下", async () => {
    fetchBoardMock.mockRejectedValue(unreadable());
    fetchHealthMock.mockResolvedValue(stalledHealth);
    await refreshBoard();
    await refreshHealth();
    renderShell();
    // §47.4 横幅在 verdict=stalled 时说这句；落进 boardError 的那一态里它整块消失
    expect(screen.getByText("Background service is stuck")).toBeTruthy();
  });

  it("有旧快照 → 旧板照常渲染，横幅一行说清等的是读权限回来", async () => {
    fetchBoardMock.mockResolvedValue(makeBoard());
    await refreshBoard();
    fetchBoardMock.mockRejectedValue(unreadable());
    await refreshBoard();
    renderShell();
    expect(screen.getByText("page-content")).toBeTruthy();
    expect(screen.getByText(UNREADABLE_TITLE)).toBeTruthy();
    expect(screen.getByText(/recovers on the background service's next write/)).toBeTruthy();
    expect(screen.queryByText(OFFLINE_TITLE)).toBeNull();
  });

  it.each(["trash", "archive", "recaps"])(
    "读看板快照的 ?page=%s 也说真话——503 下**不许**渲染成「为空」",
    async (page) => {
      // 与 boardMissing 那一态相反：404 说明文件真不在，空列表就是真话；503 说明我们
      // 不知道里面有什么，渲染成「为空」正是「拉不到不许渲染成为空」禁的那件事
      fetchBoardMock.mockRejectedValue(unreadable());
      await refreshBoard();
      goTo(`?page=${page}`);
      renderShell();
      expect(screen.getByText(UNREADABLE_TITLE)).toBeTruthy();
      expect(screen.queryByText("page-content")).toBeNull();
      expect(screen.queryByText(OFFLINE_TITLE)).toBeNull();
    },
  );

  it("SSE 正在重连也不借离线文案——owner 的 kickstart 恰好会同时掐断 SSE 流", async () => {
    // §54.1 追记 2026-09-18：`connection` 由 SSE 独立驱动，四态互斥管不到它。
    // server 刚用一个带 errno 的 503 答过话，这时说「连不上本地服务」就是把
    // 刚修掉的那句谎换个组件再说一遍——而 `launchctl kickstart`（issue #423 里
    // owner 真在用的修法）正好会造出这个组合。
    fetchBoardMock.mockResolvedValue(makeBoard());
    await refreshBoard();
    fetchBoardMock.mockRejectedValue(unreadable());
    await refreshBoard();
    setConnection("reconnecting");
    renderShell();
    expect(screen.getByText(UNREADABLE_TITLE)).toBeTruthy();
    expect(screen.queryByText(OFFLINE_TITLE)).toBeNull();
  });

  it("HTTP 取数真的失败时「连不上」仍然优先——那时它是实话", async () => {
    fetchBoardMock.mockResolvedValue(makeBoard());
    await refreshBoard();
    fetchBoardMock.mockRejectedValue(unreadable());
    await refreshBoard();
    // 下一轮拉取彻底失败（api.ts 对 fetch 抛错合成的 status 0 / READ_FAILED）
    fetchBoardMock.mockRejectedValue(
      new ApiError(0, { error: { code: "READ_FAILED", message: "Board data is temporarily unavailable." } }),
    );
    await refreshBoard();
    renderShell();
    expect(screen.getByText(OFFLINE_TITLE)).toBeTruthy();
    expect(screen.queryByText(UNREADABLE_TITLE)).toBeNull();
  });

  it("自拉快照的页不受影响（设置页照常渲染 children）", async () => {
    fetchBoardMock.mockRejectedValue(unreadable());
    await refreshBoard();
    goTo("?page=settings");
    renderShell();
    expect(screen.getByText("page-content")).toBeTruthy();
  });
});
