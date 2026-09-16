// §71.1 排队原因 chip「等电脑醒来」：睡眠闸按住的卡在运行中列是 queued 项，
// 原因 chip 由 wire 的 queued_reason 驱动（结构化 {kind:"asleep"}，扁平 token
// `machine_asleep` 同表翻译——开放枚举，未知值原样展示绝不崩渲染）。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ReactElement } from "react";
import { LanguageContext } from "../../i18n";
import { queuedReasonLabel } from "../../steer";
import type { TaskRow } from "../../types";
import { RunningCard } from "./RunningCard";

afterEach(cleanup);

const QUEUED: TaskRow = { id: "P-311", name: "等电脑醒来的卡", state: "queued" };

const wrap = (node: ReactElement, language: "zh" | "en" = "zh") =>
  render(<LanguageContext.Provider value={language}>{node}</LanguageContext.Provider>);

const zh = (a: string, b: string) => a;
const en = (a: string, b: string) => b;

describe("排队原因「等电脑醒来」（§71.1）", () => {
  it("结构化形与扁平 token 形翻译成同一句", () => {
    expect(queuedReasonLabel({ kind: "asleep" }, zh)).toBe("等电脑醒来");
    expect(queuedReasonLabel({ kind: "asleep" }, en)).toBe("waiting for the Mac to wake");
    expect(queuedReasonLabel("machine_asleep", zh)).toBe("等电脑醒来");
    expect(queuedReasonLabel("machine_asleep", en)).toBe("waiting for the Mac to wake");
  });

  it("排队卡把它渲染成 chip", () => {
    wrap(<RunningCard row={{ ...QUEUED, queued_reason: { kind: "asleep" } }} />);
    expect(screen.getByText("排队中")).toBeTruthy();
    expect(screen.getByText("等电脑醒来")).toBeTruthy();
  });

  it("并发排队的老文案一字不动", () => {
    wrap(<RunningCard row={{ ...QUEUED, queued_reason: { kind: "concurrency" } }} />);
    expect(screen.getByText("等并发位")).toBeTruthy();
    expect(screen.queryByText("等电脑醒来")).toBeNull();
  });
});
