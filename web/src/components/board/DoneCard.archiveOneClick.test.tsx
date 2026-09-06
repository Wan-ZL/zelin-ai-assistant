// 阶段性完成卡「永久完成」一点即发（原生 Cards.swift:1540-1548「One tap, no confirm (reversible via 放回看板)」；
// CONTRACT §41 / §54.1 追记）：不弹确认弹窗，直接 POST {action:"archive", comment:null, id}（§3 四键形，动作回传送主键）；
// 与潜在任务卡的同一动词（DebtCardItem，§54.4 2026-09-03 追记 (e)「可逆不弹确认」）一致——两面同一张脸。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { postAction } from "../../api";
import { resetStoreForTests } from "../../store";
import type { TaskRow } from "../../types";
import { DoneCard } from "./DoneCard";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true }),
  fetchCard: vi.fn(async (id: string) => ({ id })),
}));

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(postAction).mockClear();
});
afterEach(cleanup);

const row: TaskRow = {
  id: "P-520",
  work_id: "R-520",
  display_id: "R-520",
  id_kind: "work",
  name: "已验收的任务",
  state: "delivered",
  accepted_at: Math.floor(Date.now() / 1000) - 3600,
};

describe("DoneCard 永久完成 — one tap, no confirm", () => {
  it("点「永久完成」不开任何 <dialog>，直接发 archive（主键 id，不是展示编号）", async () => {
    render(<DoneCard row={row} />);
    fireEvent.click(screen.getByRole("button", { name: "Done for good" }));
    expect(document.querySelector("dialog")).toBeNull();
    await waitFor(() => expect(postAction).toHaveBeenCalledTimes(1));
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "archive", comment: null, id: "P-520" });
  });

  it("提交后动作行让位给 pending 一句（不可双击重复提交）", async () => {
    render(<DoneCard row={row} />);
    fireEvent.click(screen.getByRole("button", { name: "Done for good" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Done for good" })).toBeNull());
    expect(document.querySelector(".card-pending-note")).not.toBeNull();
  });

  it("按钮 tooltip 说明封存语义与可逆性（取代此前的确认弹窗正文）", () => {
    render(<DoneCard row={row} />);
    const title = screen.getByRole("button", { name: "Done for good" }).getAttribute("title") ?? "";
    expect(title).toMatch(/Seal this accepted thread/);
    expect(title).toMatch(/put it back from Done for good/);
  });
});
