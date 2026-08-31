// fork 组件自带行为测试（BUILD-CONTRACT §0.5：搬来的组件必须自带行为测试）。
// 覆盖：单选（选中即关+onChange）、多选（保持打开+按成员勾选）、Escape 关闭。
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { TaskPropertyPicker, type TaskPropertyOption } from "./TaskPropertyPicker";

afterEach(cleanup); // globals 关着，testing-library 不会自动 cleanup

const OPTIONS: readonly TaskPropertyOption<string>[] = [
  { value: "a", label: "Alpha" },
  { value: "b", label: "Beta" },
];

function Harness({
  onChange,
  selectedValues,
}: {
  onChange: (value: string) => void;
  selectedValues?: string[];
}) {
  const [open, setOpen] = useState(false);
  return (
    <TaskPropertyPicker
      value="a"
      options={OPTIONS}
      open={open}
      selectedValues={selectedValues}
      onOpenChange={setOpen}
      onChange={onChange}
      triggerClassName="chip"
      ariaLabel="pick"
    />
  );
}

describe("TaskPropertyPicker（fork）", () => {
  it("单选：点开弹层，选项点击 → onChange 且弹层关闭", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "pick" }));
    expect(screen.getByRole("listbox")).toBeTruthy();

    fireEvent.click(screen.getByRole("option", { name: "Beta" }));
    expect(onChange).toHaveBeenCalledExactlyOnceWith("b");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("多选（selectedValues）：选中后弹层保持打开，勾选按成员判定", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} selectedValues={["a"]} />);

    fireEvent.click(screen.getByRole("button", { name: "pick" }));
    expect(screen.getByRole("option", { name: /Alpha/ }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("option", { name: /Beta/ }).getAttribute("aria-selected")).toBe("false");

    fireEvent.click(screen.getByRole("option", { name: /Beta/ }));
    expect(onChange).toHaveBeenCalledExactlyOnceWith("b");
    expect(screen.getByRole("listbox")).toBeTruthy(); // toggle 由父层做，弹层不关
  });

  it("Escape 关闭弹层", () => {
    render(<Harness onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "pick" }));
    expect(screen.getByRole("listbox")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
  });
});
