// 回收站行的「恢复」是每行一个 useSubmit（原生 beginReturn + returningLocal 的 180 s 超时，Store.swift:731-739 /
// :563-565）：恢复中显示信息条、卡离开 trash 才解锁、180 s 没动 → 原生「恢复超时」句 + 行恢复可操作；
// 「永久保存」不锁行（Store.swift:794-795 badge flips in place）：POST 成功章先翻、钮隐去、「恢复」照旧可点；本地章在 backend 回
// permanent 后退场（PendingSweep.swift:277-279）、180 s 没确认则收回。行标题走 §37 链
// （原生 TrashItem.displaySummary，Cards.swift:2594）：钦定名 > summary > display_title > title；搜索也搜 display_title。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard, postAction } from "../api";
import { CONFIRM_TIMEOUT_MS } from "../components/board/boardActions";
import { LanguageContext } from "../i18n";
import { refreshBoard, resetStoreForTests } from "../store";
import type { Board, TrashRow } from "../types";
import { TrashPage, trashRowMatches } from "./TrashPage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, fetchBoard: vi.fn(), postAction: vi.fn() };
});

const rows: TrashRow[] = [
  { id: "R-201", title: "自动回复 bot", summary: "不想要自动回复。", display_title: "自动回复 bot", permanent: false,
    trashed_at: "2026-08-28T00:00:00Z", trash_reason: "rejected", purge_at: null },
  { id: "R-202", title: "旧标题", summary: "LLM 写的旧摘要", display_title: "我改的名字", user_titled: true, permanent: false,
    trashed_at: "2026-08-27T00:00:00Z", trash_reason: "deleted", purge_at: null },
];

function board(trash: TrashRow[], generated_at = "2026-09-05T10:00:00Z"): Board {
  return { generated_at, counts: { trash: trash.length }, needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash } as unknown as Board;
}

async function load(b: Board) {
  vi.mocked(fetchBoard).mockResolvedValue(b);
  await act(async () => {
    await refreshBoard();
  });
}

const wrap = (language: "zh" | "en" = "zh") => render(<LanguageContext.Provider value={language}><TrashPage /></LanguageContext.Provider>);

beforeEach(async () => {
  window.history.replaceState(null, "", "/?page=trash");
  resetStoreForTests();
  vi.mocked(postAction).mockReset().mockResolvedValue({});
  vi.useFakeTimers();
  await load(board(rows));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("TrashPage 恢复的真信号 + 180 s 兜底", () => {
  it("点「恢复」→ 行显示原生「恢复中，卡片将回到原状态列」、按钮行收起；新快照卡仍在 trash → 仍在等；卡离开 trash → 行消失", async () => {
    wrap();
    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: "恢复" })[0]);
    });
    expect(screen.getByText("恢复中，卡片将回到原状态列")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "恢复" })).toHaveLength(1); // 只剩 R-202 的

    await load(board(rows, "2026-09-05T10:00:10Z")); // actd 例行重写，R-201 还在回收站
    expect(screen.getByText("恢复中，卡片将回到原状态列")).toBeTruthy();

    await load(board([rows[1]], "2026-09-05T10:00:20Z")); // 真信号：R-201 离开 trash
    expect(screen.queryByText("恢复中，卡片将回到原状态列")).toBeNull();
    expect(screen.queryByText("自动回复")).toBeNull();
  });

  it("180 s 没动 → 原生「恢复超时，卡片仍在回收站，可重试（检查 actd 是否在运行）」+ 行恢复可操作（不再永远挂着）", async () => {
    wrap();
    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: "恢复" })[0]);
    });
    expect(screen.getByText("恢复中，卡片将回到原状态列")).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(CONFIRM_TIMEOUT_MS - 1);
    });
    expect(screen.getByText("恢复中，卡片将回到原状态列")).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(screen.queryByText("恢复中，卡片将回到原状态列")).toBeNull();
    expect(screen.getByRole("alert").textContent).toBe("恢复超时，卡片仍在回收站，可重试（检查 actd 是否在运行）");
    expect(screen.getAllByRole("button", { name: "恢复" })).toHaveLength(2); // 可重试
    fireEvent.click(screen.getAllByRole("button", { name: "恢复" })[0]);
    expect(postAction).toHaveBeenCalledTimes(2);
  });

  it("「永久保存」不锁行（原生 badge flips in place）：章先翻、钮隐去、「恢复」照旧可点；backend 回 permanent 后本地标记退场、章仍在；180 s 没确认 → 章收回", async () => {
    wrap();
    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: "永久保存" })[0]);
    });
    expect(screen.queryByText("已提交…")).toBeNull();          // 不是换列动词，没有信息条
    expect(screen.getAllByRole("button", { name: "恢复" })).toHaveLength(2); // 「恢复」没被收起
    expect(screen.getAllByRole("button", { name: "永久保存" })).toHaveLength(1); // 本行的钮随章隐去
    expect(screen.getAllByText("永久")).toHaveLength(1);
    expect(screen.getAllByText("已永久保留")).toHaveLength(1);

    await load(board([{ ...rows[0], permanent: true }, rows[1]], "2026-09-05T10:00:10Z"));
    expect(screen.getAllByText("永久")).toHaveLength(1); // permanent 为真 = 落地，章由 backend 撑着
    expect(screen.getAllByRole("button", { name: "永久保存" })).toHaveLength(1); // 只剩 R-202 的

    // 另一行：pin 提交后 backend 始终不认 → 180 s 后章收回，钮回来，给诚实句；期间「恢复」一直可点
    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: "永久保存" })[0]);
    });
    expect(screen.getAllByText("永久")).toHaveLength(2);
    expect(screen.queryAllByRole("button", { name: "永久保存" })).toHaveLength(0);
    expect(screen.getAllByRole("button", { name: "恢复" })).toHaveLength(2);
    act(() => {
      vi.advanceTimersByTime(CONFIRM_TIMEOUT_MS);
    });
    expect(screen.getAllByText("永久")).toHaveLength(1);
    expect(screen.getByRole("alert").textContent).toBe("后台响应超时，卡片已恢复可操作");
    expect(screen.getAllByRole("button", { name: "永久保存" })).toHaveLength(1);
  });

  it("pin 在途（POST 未落定）时钮禁点防双发；POST 被拒 → 章不翻、钮回来、错误句露出", async () => {
    let resolve: (v: unknown) => void = () => {};
    vi.mocked(postAction).mockImplementationOnce(() => new Promise((r) => { resolve = r; }));
    wrap();
    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: "永久保存" })[0]);
    });
    const pinButtons = screen.getAllByRole("button", { name: "永久保存" }) as HTMLButtonElement[];
    expect(pinButtons).toHaveLength(2);
    expect(pinButtons[0].disabled).toBe(true);   // 在途
    expect(pinButtons[1].disabled).toBe(false);  // 别的行不受影响
    expect(screen.queryAllByText("永久")).toHaveLength(0); // 章要等 POST 成功
    await act(async () => {
      resolve({});
    });
    expect(screen.getAllByText("永久")).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "永久保存" })).toHaveLength(1);

    // 另一行：server 拒了 → 没有章、钮可再点
    vi.mocked(postAction).mockRejectedValueOnce(new Error("inbox write failed"));
    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: "永久保存" })[0]);
    });
    expect(screen.getAllByText("永久")).toHaveLength(1);
    expect(screen.getByRole("alert").textContent).toBe("inbox write failed");
    expect((screen.getAllByRole("button", { name: "永久保存" })[0] as HTMLButtonElement).disabled).toBe(false);
  });

  it("en：恢复中 / 超时句同形", async () => {
    wrap("en");
    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: "Restore" })[0]);
    });
    expect(screen.getByText("Restoring — the card returns to its previous lane")).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(CONFIRM_TIMEOUT_MS);
    });
    expect(screen.getByRole("alert").textContent).toBe("Restore timed out — the card is back in the trash, try again (check that actd is running)");
  });
});

describe("TrashPage 行标题 = §37 链（原生 TrashItem.displaySummary）", () => {
  it("user_titled 行显示钦定名而不是 LLM 摘要；普通行仍显示 summary", () => {
    wrap();
    expect(screen.getByText("我改的名字")).toBeTruthy();
    expect(screen.queryByText("LLM 写的旧摘要")).toBeNull();
    expect(screen.queryByText("旧标题")).toBeNull();
    expect(screen.getByText("不想要自动回复。")).toBeTruthy();
  });

  it("搜索也命中 display_title", () => {
    wrap();
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索回收站" }), { target: { value: "我改的" } });
    expect(screen.getByText("我改的名字")).toBeTruthy();
    expect(screen.queryByText("不想要自动回复。")).toBeNull();
    expect(trashRowMatches(rows[1], "旧标题")).toBe(true);
    expect(trashRowMatches(rows[1], "旧摘要")).toBe(true);
    expect(trashRowMatches(rows[1], "没有的")).toBe(false);
  });
});
