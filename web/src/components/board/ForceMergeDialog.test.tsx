// 强制合并弹窗的键盘纪律（CONTRACT §41 2026-09-05 追记「弹窗一律按钮提交，Enter 换行（D35 同款）」；§21bis）：
//   1) 「强制合并」按钮不再挂 title="↩"——原生 ForceMergeSheet 的 .defaultAction 是退役规则，web 从未绑定它，
//      招牌摘掉（此前 parity 探针 shortcut:board.merge:return 靠这一个字形 PRESENT，是假阳性）；
//   2) 在单选项上按 Enter 不确认（没有 <form>、没有 keydown 绑定）；只有按钮走 onConfirm(primary)；
//   3) Esc 仍是 <dialog> 的 cancel 事件 → onCancel（title="⎋" 是真的，保留）。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LanguageContext } from "../../i18n";
import { ForceMergeDialog } from "./ForceMergeDialog";

function mount() {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <LanguageContext.Provider value="en">
      <ForceMergeDialog ids={["P-1", "P-2"]} titles={{ "P-1": "First", "P-2": "Second" }} onConfirm={onConfirm} onCancel={onCancel} />
    </LanguageContext.Provider>,
  );
  return {
    confirm: screen.getByRole("button", { name: "Force-merge" }) as HTMLButtonElement,
    cancel: screen.getByRole("button", { name: "Cancel" }) as HTMLButtonElement,
    onConfirm,
    onCancel,
  };
}

beforeEach(() => {
  // jsdom <dialog> 兜底（同 parity.test.tsx）：老版本没有 showModal/close
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.open = true; };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) { this.open = false; };
  }
});

afterEach(cleanup);

describe("ForceMergeDialog — button-only confirm (D35 for dialogs)", () => {
  it("the confirm button carries no ↩ tooltip; Cancel keeps its honest ⎋", () => {
    const { confirm, cancel } = mount();
    expect(confirm.getAttribute("title")).toBeNull();
    expect(cancel.getAttribute("title")).toBe("⎋");
  });

  it("Enter on a radio does not confirm; the button does, with the chosen primary", () => {
    const { confirm, onConfirm } = mount();
    const second = screen.getByRole("radio", { name: /P-2/ });
    fireEvent.click(second);
    fireEvent.keyDown(second, { key: "Enter", code: "Enter" });
    fireEvent.keyDown(confirm, { key: "Enter", code: "Enter" });
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith("P-2");
  });

  it("the dialog's cancel event (Esc) still routes to onCancel", () => {
    const { onCancel, onConfirm } = mount();
    fireEvent(document.querySelector("dialog.zai-dialog")!, new Event("cancel", { cancelable: true }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
