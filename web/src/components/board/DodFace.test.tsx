// 卡面 DoD / ☐ 验收清单 紧凑块（D43；CONTRACT §54.1 第 2 项 2026-09-06 追记）的积木判例：
//   1) dod 变体：编号「1. …」，空清单零 DOM（原生 Cards.swift:1085 `if !card.dod.isEmpty`）；
//   2) checklist 变体：永远渲染，空给兜底句「该任务未定义验收标准，请自行判断」（原生 :1858 always rendered）；
//   3) 紧凑形：最多 FACE_DOD_MAX（3）条 + 「+N」（N = 余量），恰好 3 条没有 +N；每条 title = 全文（单行截断 hover 看全）；
//   4) §64 评语：verdict 逐字 = 建议验收 → ☑ + is-ai-checked + title 点明是 AI 判断；需继续做 / 需要拍板 / 未知 / 没评 → ☐；
//   5) 非字符串 / 空白条目过滤（LLM 输出不可信，宪法第 11 条）；「+N」不是按钮（D34 单一详情面，卡上不长开合）。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LanguageContext } from "../../i18n";
import { checklistChecked, DodFace, FACE_DOD_MAX } from "./DodFace";

afterEach(cleanup);

const zh = (node: JSX.Element) => <LanguageContext.Provider value="zh">{node}</LanguageContext.Provider>;

describe("DodFace — dod variant (proposal face)", () => {
  it("编号清单 + 逐字标题；空清单零 DOM", () => {
    const { container, rerender } = render(<DodFace items={["a 条", "b 条"]} variant="dod" />);
    expect(screen.getByText("Definition of done:").className).toBe("card-dod-heading");
    const items = container.querySelectorAll(".card-dod-item");
    expect(Array.from(items).map((li) => li.textContent?.replace(/\s+/g, " ").trim())).toEqual(["1. a 条", "2. b 条"]);
    expect(items[0].getAttribute("title")).toBe("a 条");
    expect(container.querySelector(".card-dod-more")).toBeNull();
    rerender(<DodFace items={[]} variant="dod" />);
    expect(container.querySelector(".card-dod")).toBeNull();
    rerender(<DodFace items={undefined} variant="dod" />);
    expect(container.querySelector(".card-dod")).toBeNull();
  });

  it("中文标题逐字原生「怎样算办完：」", () => {
    render(zh(<DodFace items={["x"]} variant="dod" />));
    expect(screen.getByText("怎样算办完：")).toBeTruthy();
  });

  it(`最多 ${FACE_DOD_MAX} 条 + 「+N」；恰好 ${FACE_DOD_MAX} 条没有 +N；「+N」不是按钮`, () => {
    expect(FACE_DOD_MAX).toBe(3);
    const { container, rerender } = render(<DodFace items={["1", "2", "3", "4", "5"]} variant="dod" />);
    expect(container.querySelectorAll(".card-dod-item")).toHaveLength(3);
    const more = container.querySelector(".card-dod-more")!;
    expect(more.textContent).toContain("+2");
    expect(more.getAttribute("title")).toBe("2 more under Details ▸");
    expect(more.querySelector("button")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    rerender(<DodFace items={["1", "2", "3"]} variant="dod" />);
    expect(container.querySelectorAll(".card-dod-item")).toHaveLength(3);
    expect(container.querySelector(".card-dod-more")).toBeNull();
  });

  it("非字符串 / 空白条目不算（LLM 输出不可信）", () => {
    const { container } = render(<DodFace items={["ok", 42, "   ", null, { x: 1 }]} variant="dod" />);
    expect(container.querySelectorAll(".card-dod-item")).toHaveLength(1);
    expect(container.querySelector(".card-dod-more")).toBeNull();
  });
});

describe("DodFace — checklist variant (review face)", () => {
  it("永远渲染：空清单给兜底句（原生 always rendered），有条目给 ☐", () => {
    const { container, rerender } = render(zh(<DodFace items={[]} variant="checklist" />));
    expect(screen.getByText("验收清单——逐条对照：")).toBeTruthy();
    expect(screen.getByText("该任务未定义验收标准，请自行判断").className).toBe("card-dod-empty");
    rerender(zh(<DodFace items={["条一"]} variant="checklist" />));
    expect(container.querySelector(".card-dod-empty")).toBeNull();
    expect(container.querySelector(".card-dod-item")!.textContent?.replace(/\s+/g, " ").trim()).toBe("☐ 条一");
    expect(container.querySelector(".card-dod-mark")!.textContent).toBe("☐");
  });

  it("英文标题 / 兜底句逐字原生", () => {
    render(<DodFace items={[]} variant="checklist" />);
    expect(screen.getByText("Acceptance checklist:")).toBeTruthy();
    expect(screen.getByText("No acceptance criteria defined — judge manually")).toBeTruthy();
  });

  it("§64 评语 = 建议验收 → ☑ + is-ai-checked + title 点明 AI 判断；其余 verdict / 没评 → ☐", () => {
    const { container, rerender } = render(<DodFace items={["a", "b"]} variant="checklist" assessment={{ verdict: "建议验收", summary: "ok" }} />);
    expect(container.querySelector(".card-dod")!.className).toContain("is-ai-checked");
    expect(Array.from(container.querySelectorAll(".card-dod-mark")).map((m) => m.textContent)).toEqual(["☑", "☑"]);
    expect(container.querySelector(".card-dod-list")!.getAttribute("title")).toMatch(/^AI verdict “Looks done”/);
    for (const verdict of ["需继续做", "需要拍板", "somethingelse", null, undefined]) {
      rerender(<DodFace items={["a", "b"]} variant="checklist" assessment={verdict === undefined ? undefined : { verdict }} />);
      expect(container.querySelector(".card-dod")!.className).not.toContain("is-ai-checked");
      expect(Array.from(container.querySelectorAll(".card-dod-mark")).map((m) => m.textContent)).toEqual(["☐", "☐"]);
      expect(container.querySelector(".card-dod-list")!.getAttribute("title")).toBeNull();
    }
  });

  it("checklistChecked 只认逐字词表值", () => {
    expect(checklistChecked({ verdict: "建议验收" })).toBe(true);
    expect(checklistChecked({ verdict: "建议验收 " })).toBe(false);
    expect(checklistChecked({ verdict: "Looks done" })).toBe(false);
    expect(checklistChecked(null)).toBe(false);
    expect(checklistChecked(undefined)).toBe(false);
  });

  it("紧凑形同 dod：3 条 + 「+N」", () => {
    const { container } = render(<DodFace items={["1", "2", "3", "4"]} variant="checklist" />);
    expect(container.querySelectorAll(".card-dod-item")).toHaveLength(3);
    expect(container.querySelector(".card-dod-more")!.textContent).toContain("+1");
  });
});
