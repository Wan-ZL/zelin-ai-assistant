// T2 typed-confirm 弹窗的键盘纪律（CONTRACT §41 2026-09-05 追记「弹窗一律按钮提交，Enter 换行（D35 同款）」(d)；§50 W17）：
//   1) 输入框里按 Enter 不批准，也不被 preventDefault——单行 <input> 没有换行语义，Enter 什么都不做；
//      正词已键入也一样：批准只有「批准」按钮一条路（正文本就写着「请输入 确认 或 go 后再点「批准」」）；
//   2) 按钮走 approve：正词（trim + lowercase 宽容）→ onConfirm；错词 → 「上次输入不匹配。」+ 清空输入、不 onConfirm。
//   确认词校验与 wire 的判例在 ProposalCard.test.tsx，这里只钉键盘半边。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { T2ConfirmDialog } from "./T2ConfirmDialog";

function mount() {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <LanguageContext.Provider value="en">
      <T2ConfirmDialog cardId="P-7" summary="Ship it" costLine="Estimated cost: $3" onConfirm={onConfirm} onCancel={onCancel} />
    </LanguageContext.Provider>,
  );
  return {
    input: screen.getByPlaceholderText("Type 确认 or go") as HTMLInputElement,
    approve: screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement,
    onConfirm,
    onCancel,
  };
}

beforeEach(() => {
  // jsdom <dialog> 兜底（同 ForceMergeDialog.test.tsx）：老版本没有 showModal/close
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.open = true; };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) { this.open = false; };
  }
});

afterEach(cleanup);

describe("T2ConfirmDialog — button-only approve (D35 for dialogs)", () => {
  it("Enter in the input does not approve even with the right word typed, and is not preventDefault-ed", () => {
    const { input, onConfirm } = mount();
    fireEvent.change(input, { target: { value: "go" } });
    const notPrevented = fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    expect(notPrevented).toBe(true);
    fireEvent.keyDown(input, { key: "Enter", code: "Enter", metaKey: true });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter", ctrlKey: true });
    expect(onConfirm).not.toHaveBeenCalled();
    expect(input.value).toBe("go"); // 没走 approve：既没批准，也没被「不匹配」分支清空
    expect(screen.queryByText("Previous input didn't match.")).toBeNull();
  });

  it("the Approve button is the only way in: right word confirms, wrong word warns and clears", () => {
    const { input, approve, onConfirm } = mount();
    fireEvent.change(input, { target: { value: "yes" } });
    fireEvent.click(approve);
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByText("Previous input didn't match.")).toBeTruthy();
    expect(input.value).toBe("");

    fireEvent.change(input, { target: { value: "  确认 " } });
    fireEvent.click(approve);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
