// 驾驶协议的不可信边界（CONTRACT §79.2）：模型吐出来的字节进不了执行面，除非逐字段过关。
// 这一份钉的是「认不出来就拒绝，而且永远不抛」——radar 侧出过数字 title / bool deadline 的
// 真实事故，所以这里对类型一律显式判定，不靠 JS 的隐式转换。
import { describe, expect, it } from "vitest";
import {
  MAX_TEXT, MAX_WAIT_MS, PROTOCOL_VERSION, describeAction, parseAction, parseActionLine,
  scrubNumber, scrubText,
} from "./protocol";

const REFS = ["e1", "e2"];

describe("parseAction 的形状闸", () => {
  it("非对象一律拒", () => {
    for (const raw of [null, undefined, 42, "click", [], true]) {
      expect(parseAction(raw, REFS).ok).toBe(false);
    }
  });

  it("未知动词拒，并把原值写进理由", () => {
    const parsed = parseAction({ verb: "drag" }, REFS);
    expect(parsed).toEqual({ ok: false, reason: '未知动词 "drag"' });
  });

  it("verb 不是字符串也拒（数字 verb 真的会出现）", () => {
    expect(parseAction({ verb: 7 }, REFS).ok).toBe(false);
  });

  it("多出来的键一律忽略，不算错", () => {
    const parsed = parseAction({ verb: "click", ref: "e1", selector: ".task-card", why: "因为" }, REFS);
    expect(parsed).toEqual({ ok: true, action: { verb: "click", ref: "e1" } });
  });
});

describe("ref 是唯一的越权闸", () => {
  it("本步没发出去的 ref 点不了", () => {
    expect(parseAction({ verb: "click", ref: "e99" }, REFS).ok).toBe(false);
  });

  it("ref 不是字符串也点不了", () => {
    expect(parseAction({ verb: "click", ref: 1 }, REFS).ok).toBe(false);
  });

  it("发过的 ref 才放行", () => {
    expect(parseAction({ verb: "click", ref: "e2" }, REFS)).toEqual({
      ok: true, action: { verb: "click", ref: "e2" },
    });
  });

  it("type 同样受这道闸管", () => {
    expect(parseAction({ verb: "type", ref: "nope", text: "x" }, REFS).ok).toBe(false);
  });
});

describe("文本消毒", () => {
  it("非字符串归零，不是抛", () => {
    expect(scrubText(42)).toBe("");
    expect(scrubText(null)).toBe("");
    expect(scrubText({ toString: () => "x" })).toBe("");
  });

  it("控制字符去掉，换行和制表留着", () => {
    // NUL 与 BEL 写成 fromCharCode：源码里不留控制字节（留了 git 会当二进制文件）
    const nasty = "a" + String.fromCharCode(0) + "b" + String.fromCharCode(7) + "c\nd\te";
    expect(scrubText(nasty)).toBe("abc\nd\te");
  });

  it("超长截断到 MAX_TEXT", () => {
    expect(scrubText("x".repeat(MAX_TEXT + 500)).length).toBe(MAX_TEXT);
  });

  it("type 的 text 缺席时是空串，动作仍然成立", () => {
    expect(parseAction({ verb: "type", ref: "e1" }, REFS)).toEqual({
      ok: true, action: { verb: "type", ref: "e1", text: "" },
    });
  });
});

describe("数字消毒", () => {
  it("十进制数字串认，别的不认", () => {
    expect(scrubNumber("250")).toBe(250);
    expect(scrubNumber(" 12.5 ")).toBe(12.5);
    expect(scrubNumber("250ms")).toBe(null);
    expect(scrubNumber("")).toBe(null);
    expect(scrubNumber(true)).toBe(null);
  });

  it("NaN / Infinity 不是数字", () => {
    expect(scrubNumber(Number.NaN)).toBe(null);
    expect(scrubNumber(Number.POSITIVE_INFINITY)).toBe(null);
  });

  it("wait 夹在 [0, MAX_WAIT_MS]", () => {
    expect(parseAction({ verb: "wait", ms: -5 }, REFS)).toEqual({ ok: true, action: { verb: "wait", ms: 0 } });
    expect(parseAction({ verb: "wait", ms: 10 ** 9 }, REFS)).toEqual({
      ok: true, action: { verb: "wait", ms: MAX_WAIT_MS },
    });
    expect(parseAction({ verb: "wait", ms: "600" }, REFS)).toEqual({ ok: true, action: { verb: "wait", ms: 600 } });
  });

  it("wait 没有 ms 就拒", () => {
    expect(parseAction({ verb: "wait" }, REFS).ok).toBe(false);
  });
});

describe("词表型字段", () => {
  it("press 只认词表里的键", () => {
    expect(parseAction({ verb: "press", key: "Enter" }, REFS)).toEqual({
      ok: true, action: { verb: "press", key: "Enter" },
    });
    expect(parseAction({ verb: "press", key: "Meta+Q" }, REFS).ok).toBe(false);
    expect(parseAction({ verb: "press", key: "a" }, REFS).ok).toBe(false);
  });

  it("goto 只认页面词表（空串 = 看板）", () => {
    expect(parseAction({ verb: "goto", page: "" }, REFS)).toEqual({ ok: true, action: { verb: "goto", page: "" } });
    expect(parseAction({ verb: "goto", page: "settings" }, REFS).ok).toBe(true);
    expect(parseAction({ verb: "goto", page: "https://example.com" }, REFS).ok).toBe(false);
    expect(parseAction({ verb: "goto" }, REFS).ok).toBe(false);
  });
});

describe("report / done / give_up", () => {
  it("没有 summary 的 report 不算一条发现（数字 summary 同理）", () => {
    expect(parseAction({ verb: "report", detail: "有问题" }, REFS).ok).toBe(false);
    expect(parseAction({ verb: "report", summary: 404 }, REFS).ok).toBe(false);
  });

  it("认不出的严重度退回 warn，而不是拒掉整条发现", () => {
    expect(parseAction({ verb: "report", summary: "计数不对", severity: "critical" }, REFS)).toEqual({
      ok: true, action: { verb: "report", severity: "warn", summary: "计数不对", detail: "" },
    });
  });

  it("合法严重度原样留着", () => {
    const parsed = parseAction({ verb: "report", summary: "炸了", severity: "error", detail: "堆栈" }, REFS);
    expect(parsed).toEqual({
      ok: true, action: { verb: "report", severity: "error", summary: "炸了", detail: "堆栈" },
    });
  });

  it("done / give_up 没有 summary 也成立", () => {
    expect(parseAction({ verb: "done" }, REFS)).toEqual({ ok: true, action: { verb: "done", summary: "" } });
    expect(parseAction({ verb: "give_up", summary: "找不到" }, REFS)).toEqual({
      ok: true, action: { verb: "give_up", summary: "找不到" },
    });
  });
});

describe("parseActionLine", () => {
  it("一行合法 JSON、step 回声也对，才是一条动作", () => {
    expect(parseActionLine('{"verb":"click","ref":"e1","step":3}', REFS, 3)).toEqual({
      ok: true, action: { verb: "click", ref: "e1" },
    });
  });

  it("空行 / 坏 JSON / 一段散文都只是拒绝理由，不抛", () => {
    expect(parseActionLine("   ", REFS, 1).ok).toBe(false);
    expect(parseActionLine("{verb: click}", REFS, 1).ok).toBe(false);
    expect(parseActionLine("我觉得应该点第一个按钮", REFS, 1).ok).toBe(false);
  });

  it("JSON 数组也不是动作", () => {
    expect(parseActionLine('[{"verb":"click","ref":"e1"}]', REFS, 1).ok).toBe(false);
  });
});

// 这一组钉的是一个真事故形状：驾驶员超时之后，那一行答案照样会漂回来，于是它成了**下一问**
// 的答案。ref 闸挡不住——ref 每步按文档序重编，上一步的 `e1` 在这一步几乎总能对上号，只是
// 指向了另一个元素，结果是巡检自己点出来的状态变化被当成产品 bug 报上去。
describe("一问一答的配对闸", () => {
  it("step 对不上就丢，理由里把两个数都说出来", () => {
    const parsed = parseActionLine('{"verb":"click","ref":"e1","step":3}', REFS, 4);
    expect(parsed.ok).toBe(false);
    expect((parsed as { reason: string }).reason).toContain("3");
    expect((parsed as { reason: string }).reason).toContain("4");
  });

  it("压根没回声也算对不上——不回声就等于把这道闸关了", () => {
    expect(parseActionLine('{"verb":"click","ref":"e1"}', REFS, 1).ok).toBe(false);
  });

  it("回声不是数字（null / bool / 一句话）一律当没回", () => {
    for (const echo of ["null", "true", '"3"x', "{}"]) {
      expect(parseActionLine(`{"verb":"wait","ms":1,"step":${echo}}`, REFS, 3).ok).toBe(false);
    }
  });

  it("数字串形式的回声认（JSON 本身没这毛病，但驾驶员可能是任何语言写的）", () => {
    expect(parseActionLine('{"verb":"wait","ms":1,"step":"3"}', REFS, 3).ok).toBe(true);
  });

  it("配对先于动词——第 0 步的陈年回答连解析都轮不到", () => {
    const parsed = parseActionLine('{"verb":"drag","ref":"e1","step":0}', REFS, 9);
    expect(parsed.ok).toBe(false);
    expect((parsed as { reason: string }).reason).toContain("答非所问");
  });
});

describe("describeAction", () => {
  it("每个动词都有一行人话（复现步骤照着写）", () => {
    expect(describeAction({ verb: "click", ref: "e3" })).toBe("click e3");
    expect(describeAction({ verb: "type", ref: "e1", text: "你好" })).toBe('type e1 "你好"');
    expect(describeAction({ verb: "press", key: "Escape" })).toBe("press Escape");
    expect(describeAction({ verb: "wait", ms: 400 })).toBe("wait 400ms");
    expect(describeAction({ verb: "goto", page: "trash" })).toBe("goto ?page=trash");
    expect(describeAction({ verb: "report", severity: "warn", summary: "歪了", detail: "" }))
      .toBe("report[warn] 歪了");
    expect(describeAction({ verb: "done", summary: "到了" })).toBe("done 到了");
  });
});

it("协议版本是个正整数，且随 Observation 一起发出去", () => {
  expect(Number.isInteger(PROTOCOL_VERSION)).toBe(true);
  expect(PROTOCOL_VERSION).toBeGreaterThan(0);
});
