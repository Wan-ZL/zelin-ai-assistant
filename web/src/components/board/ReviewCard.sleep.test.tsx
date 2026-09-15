// §71.2 诚实耗时：待验收卡的「耗时」旁边必须说出电脑睡掉的那几小时。
// issue #311：「卡面上的『耗时 4 小时 52 分』数的是电脑睡着的时间。」
// wire key `slept_seconds` 逐字镜像（防腐 #10）；缺席 / 非正 / 坏形状一个字都不多。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ReactElement } from "react";
import { LanguageContext } from "../../i18n";
import type { ReviewCard as ReviewCardRow } from "../../types";
import { ReviewCard } from "./ReviewCard";

afterEach(cleanup);

const BASE: ReviewCardRow = {
  id: "R-296",
  name: "夜里被睡眠打断的卡",
  dod: [],
  delivery_mode: "repo",
  dispatched_at: 1_788_000_000,
  review_at: 1_788_017_520,   // 4 小时 52 分后被收割
};

const wrap = (node: ReactElement, language: "zh" | "en" = "zh") =>
  render(<LanguageContext.Provider value={language}>{node}</LanguageContext.Provider>);

describe("ReviewCard 的睡眠注脚（§71.2）", () => {
  it("有 slept_seconds 时在耗时旁边说出来", () => {
    wrap(<ReviewCard card={{ ...BASE, slept_seconds: 17_400 }} />);
    expect(screen.getByText("4小时52分")).toBeTruthy();          // 既有的「耗时」
    expect(screen.getByText("其中 4小时50分 电脑睡眠")).toBeTruthy();
  });

  it("英文面同一句", () => {
    wrap(<ReviewCard card={{ ...BASE, slept_seconds: 17_400 }} />, "en");
    expect(screen.getByText("4h 50m of it asleep")).toBeTruthy();
  });

  it("没睡过的卡不多一个字", () => {
    wrap(<ReviewCard card={BASE} />);
    expect(screen.queryByText(/电脑睡眠/)).toBeNull();
  });

  it("坏形状（0 / 负数 / 字符串 / NaN）一律不渲染", () => {
    for (const bad of [0, -1, "9999", NaN]) {
      wrap(<ReviewCard card={{ ...BASE, slept_seconds: bad as number }} />);
      expect(screen.queryByText(/电脑睡眠/)).toBeNull();
      cleanup();
    }
  });
});
