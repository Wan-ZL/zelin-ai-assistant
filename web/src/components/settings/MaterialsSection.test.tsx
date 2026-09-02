// 设置页「素材库」section（CONTRACT §62，D11）：
//   1) 挂载即拉 status=open，按钮显示开放计数；
//   2) 加入 = POST 只带 url/note 两键（trim 后）、成功清空输入 + toast、重拉列表；两者皆空按钮禁用；
//   3) server 400 的整句原文以 toast(role=alert) 显示；
//   4) 弹窗只渲染 server 给的（已按 open 过滤的）条目，可滚动容器，每行 备注/链接/状态/相对时间 + 放弃；
//   5) 放弃 = POST {id} → 重拉；空列表有空态文案；读失败渲染错误而不是空白。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchMaterials, postMaterialAdd, postMaterialDismiss } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { MaterialItem, MaterialsList } from "../../types";
import { MaterialsSection } from "./MaterialsSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    fetchMaterials: vi.fn(),
    postMaterialAdd: vi.fn(),
    postMaterialDismiss: vi.fn(),
  };
});

function item(over: Partial<MaterialItem> = {}): MaterialItem {
  return {
    id: "m-000000000001",
    ts: "2026-09-02T10:00:00Z",
    created_at: "2026-09-02T10:00:00Z",
    url: "https://example.com/talk",
    note: "Uncle Bob on CRAP",
    status: "new",
    links: {},
    ...over,
  };
}

function list(items: MaterialItem[], total = items.length): MaterialsList {
  return { items, status: "open", counts: { open: items.length, total } };
}

function renderSection() {
  return render(
    <LanguageContext.Provider value="en">
      <MaterialsSection />
    </LanguageContext.Provider>,
  );
}

beforeEach(() => {
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
      this.open = true;
    };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
      this.open = false;
    };
  }
  resetStoreForTests();
  vi.mocked(fetchMaterials).mockReset().mockResolvedValue(list([item()], 3));
  vi.mocked(postMaterialAdd).mockReset();
  vi.mocked(postMaterialDismiss).mockReset();
});

afterEach(cleanup);

describe("MaterialsSection", () => {
  it("fetches the open list on mount and shows the count on the button", async () => {
    renderSection();
    await screen.findByRole("button", { name: "Show pending (1)" });
    expect(fetchMaterials).toHaveBeenCalledWith("open");
    expect(screen.getByRole("heading", { name: "Materials box" })).toBeTruthy();
    // add is disabled while both fields are empty
    expect((screen.getByRole("button", { name: "Add" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("add POSTs exactly url+note (trimmed), clears the inputs, toasts and refetches", async () => {
    vi.mocked(postMaterialAdd).mockResolvedValue(item({ id: "m-000000000002", note: "new one" }));
    vi.mocked(fetchMaterials)
      .mockResolvedValueOnce(list([item()]))
      .mockResolvedValue(list([item({ id: "m-000000000002", note: "new one" }), item()]));
    renderSection();
    const url = (await screen.findByLabelText("Link")) as HTMLInputElement;
    const note = screen.getByLabelText("One-line note") as HTMLInputElement;
    fireEvent.change(url, { target: { value: "  https://youtu.be/abc " } });
    fireEvent.change(note, { target: { value: " why it matters " } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await screen.findByText(/Added — the daily loop will pick it up/);
    expect(postMaterialAdd).toHaveBeenCalledTimes(1);
    const body = vi.mocked(postMaterialAdd).mock.calls[0][0];
    expect(body).toEqual({ url: "https://youtu.be/abc", note: "why it matters" });
    expect(Object.keys(body)).toHaveLength(2);
    expect(url.value).toBe("");
    expect(note.value).toBe("");
    await screen.findByRole("button", { name: "Show pending (2)" });
    expect(fetchMaterials).toHaveBeenCalledTimes(2);
  });

  it("a note alone is enough to enable Add; Enter submits the form", async () => {
    vi.mocked(postMaterialAdd).mockResolvedValue(item({ url: "", note: "just a thought" }));
    renderSection();
    const note = (await screen.findByLabelText("One-line note")) as HTMLInputElement;
    fireEvent.change(note, { target: { value: "just a thought" } });
    const add = screen.getByRole("button", { name: "Add" }) as HTMLButtonElement;
    expect(add.disabled).toBe(false);
    fireEvent.submit(note.closest("form") as HTMLFormElement);
    await waitFor(() => expect(postMaterialAdd).toHaveBeenCalledWith({ url: "", note: "just a thought" }));
  });

  it("surfaces the server's validation sentence as an alert toast and keeps the draft", async () => {
    vi.mocked(postMaterialAdd).mockRejectedValue(new ApiError(400, {
      error: { code: "INVALID_FIELD", message: "url must be an http(s) address of at most 2048 characters", details: { reason: "invalid" } },
    }));
    renderSection();
    const url = (await screen.findByLabelText("Link")) as HTMLInputElement;
    fireEvent.change(url, { target: { value: "ftp://nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    const toast = await screen.findByRole("alert");
    expect(toast.textContent).toContain("url must be an http(s) address");
    expect(toast.className).toContain("is-error");
    expect(url.value).toBe("ftp://nope");
  });

  it("the popup lists the server's open items with note, link, status, age and a dismiss button", async () => {
    vi.mocked(fetchMaterials).mockResolvedValue(list([
      item(),
      item({ id: "m-000000000002", note: "", url: "https://example.com/only-link", status: "picked_up" }),
      item({ id: "m-000000000003", url: "", note: "note only", status: "proposal_created", links: { proposal_id: "P-210" } }),
    ]));
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "Show pending (3)" }));

    const dialog = screen.getByRole("dialog", { hidden: true });
    expect(within(dialog).getByText("Materials box · pending")).toBeTruthy();
    const rows = within(dialog).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText("Uncle Bob on CRAP")).toBeTruthy();
    const link = within(rows[0]).getByRole("link") as HTMLAnchorElement;
    expect(link.href).toBe("https://example.com/talk");
    expect(link.target).toBe("_blank");
    expect(link.rel).toContain("noopener");
    expect(within(rows[0]).getByText("New")).toBeTruthy();
    expect(within(rows[1]).getByText("Picked up by the loop")).toBeTruthy();
    expect(within(rows[1]).queryByRole("link")?.textContent).toBe("https://example.com/only-link");
    expect(within(rows[2]).getByText("Proposal created")).toBeTruthy();
    expect(within(rows[2]).getByText("P-210")).toBeTruthy();
    expect(within(rows[2]).queryByRole("link")).toBeNull();
    expect(within(dialog).getAllByRole("button", { name: /^Dismiss / })).toHaveLength(3);
    // the list container is the scroll surface (CSS class pinned; max-height lives in settings.css)
    expect(within(dialog).getByRole("list").className).toBe("materials-list");
  });

  it("dismiss POSTs the id, refetches, and the row is gone", async () => {
    vi.mocked(fetchMaterials)
      .mockResolvedValueOnce(list([item(), item({ id: "m-000000000002", note: "keep" })]))
      .mockResolvedValue(list([item({ id: "m-000000000002", note: "keep" })], 2));
    vi.mocked(postMaterialDismiss).mockResolvedValue(item({ status: "dismissed" }));
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "Show pending (2)" }));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss Uncle Bob on CRAP" }));

    await waitFor(() => expect(postMaterialDismiss).toHaveBeenCalledWith("m-000000000001"));
    await waitFor(() => expect(screen.queryByText("Uncle Bob on CRAP")).toBeNull());
    expect(screen.getByText("keep")).toBeTruthy();
    await screen.findByRole("button", { name: "Show pending (1)" });
  });

  it("dismiss failure toasts the server sentence", async () => {
    vi.mocked(postMaterialDismiss).mockRejectedValue(new ApiError(409, {
      error: { code: "CONFLICT", message: "new → dismissed is not allowed", details: { reason: "bad_transition" } },
    }));
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "Show pending (1)" }));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss Uncle Bob on CRAP" }));
    expect((await screen.findByRole("alert")).textContent).toContain("is not allowed");
  });

  it("empty open list shows the empty state and Close closes the popup", async () => {
    vi.mocked(fetchMaterials).mockResolvedValue(list([], 5));
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "Show pending (0)" }));
    expect(screen.getByText("Empty — drop something in.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText("Materials box · pending")).toBeNull();
  });

  it("read failure renders the error instead of a blank section", async () => {
    vi.mocked(fetchMaterials).mockRejectedValue(new ApiError(0, {
      error: { code: "READ_FAILED", message: "Board data is temporarily unavailable." },
    }));
    renderSection();
    expect((await screen.findByRole("alert")).textContent).toContain("temporarily unavailable");
    // the form still works offline-ish: nothing crashes, add stays available once typed
    fireEvent.change(screen.getByLabelText("One-line note"), { target: { value: "x" } });
    expect((screen.getByRole("button", { name: "Add" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
