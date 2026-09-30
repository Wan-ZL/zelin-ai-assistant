// 离线驾驶员的策略（CONTRACT §79.2）：同一条循环，确定性的那一版。
// 钉三件事：按剧本走、可选步骤找不到就跳过不浪费步、等不到的步骤会**认栽**而不是空转到
// 步数上限（行程表写错和界面真变了，两种都该留痕）。
import { describe, expect, it } from "vitest";
import { INITIAL_STATE, MAX_STALLS, scriptedAction, type ScriptedState } from "./scripted";
import type { Journey } from "./journeys";
import type { ElementRef, Observation } from "./protocol";

function obs(elements: Partial<ElementRef>[]): Observation {
  return {
    protocol: 1, journey: "t", goal: "g", notes: "", step: 1, maxSteps: 10,
    url: "http://127.0.0.1:1/", page: "board", lang: "zh-CN",
    viewport: { width: 1440, height: 900 }, screenshot: "", text: "",
    lastAction: null, lastError: null, settling: false,
    elements: elements.map((element, index) => ({
      ref: `e${index + 1}`, role: "button", name: "", tag: "button",
      enabled: true, editable: false, ...element,
    })),
  };
}

function journey(hints: Journey["hints"]): Journey {
  return {
    name: "t", scene: "initial", lang: "zh", viewport: { width: 1440, height: 900 },
    goal: "g", notes: "", maxSteps: 10, stepBudgetMs: 1000,
    check: { kind: "none" }, benignAlerts: [], hints,
  };
}

describe("按剧本走", () => {
  it("不需要指认元素的步骤直接发，cursor 前进", () => {
    const step = scriptedAction(journey([{ do: "wait", ms: 250 }]), obs([]), INITIAL_STATE);
    expect(step.action).toEqual({ verb: "wait", ms: 250 });
    expect(step.state).toEqual({ cursor: 1, stalls: 0 });
  });

  it("wait 没写 ms 时有个默认值", () => {
    expect(scriptedAction(journey([{ do: "wait" }]), obs([]), INITIAL_STATE).action)
      .toEqual({ verb: "wait", ms: 300 });
  });

  it("按 `<role> <name>` 整串匹配，找到就点", () => {
    const j = journey([{ want: "^button 捕获$", do: "click" }]);
    const step = scriptedAction(j, obs([{ name: "直跑" }, { name: "捕获" }]), INITIAL_STATE);
    expect(step.action).toEqual({ verb: "click", ref: "e2" });
  });

  it("停用的元素不算找到", () => {
    const j = journey([{ want: "捕获", do: "click" }]);
    const step = scriptedAction(j, obs([{ name: "捕获", enabled: false }]), INITIAL_STATE);
    expect(step.action).toEqual({ verb: "wait", ms: 400 });
  });

  it("type 只认可编辑的元素", () => {
    const j = journey([{ want: "提案", do: "type", text: "一句话" }]);
    const nope = scriptedAction(j, obs([{ name: "提案", editable: false }]), INITIAL_STATE);
    expect(nope.action.verb).toBe("wait");
    const yes = scriptedAction(j, obs([
      { name: "提案", editable: false },
      { name: "一句话，AI 来研究并提案…", role: "textbox", tag: "textarea", editable: true },
    ]), INITIAL_STATE);
    expect(yes.action).toEqual({ verb: "type", ref: "e2", text: "一句话" });
  });

  it("剧本走完就 done", () => {
    const step = scriptedAction(journey([]), obs([]), INITIAL_STATE);
    expect(step.action).toEqual({ verb: "done", summary: "剧本走完" });
  });
});

describe("可选步骤", () => {
  it("找不到就跳过，同一步里接着找下一条能做的", () => {
    const j = journey([
      { want: "不存在的按钮", do: "click", optional: true },
      { do: "press", key: "Escape" },
    ]);
    const step = scriptedAction(j, obs([]), INITIAL_STATE);
    expect(step.action).toEqual({ verb: "press", key: "Escape" });
    expect(step.state.cursor).toBe(2);
  });

  it("全是可选且都找不到 → 直接 done，不浪费步数", () => {
    const j = journey([{ want: "无", do: "click", optional: true }, { want: "也无", do: "click", optional: true }]);
    expect(scriptedAction(j, obs([]), INITIAL_STATE).action.verb).toBe("done");
  });
});

describe("等不到就认栽", () => {
  const j = journey([{ want: "永远不出现", do: "click" }]);

  it("前几次只是等一等（界面可能还在渲染）", () => {
    let state: ScriptedState = INITIAL_STATE;
    for (let i = 1; i < MAX_STALLS; i += 1) {
      const step = scriptedAction(j, obs([]), state);
      expect(step.action).toEqual({ verb: "wait", ms: 400 });
      expect(step.state.stalls).toBe(i);
      state = step.state;
    }
  });

  it("连着 MAX_STALLS 次还没有就 give_up，理由里带上等的是什么", () => {
    let state: ScriptedState = INITIAL_STATE;
    let action = scriptedAction(j, obs([]), state).action;
    for (let i = 0; i < MAX_STALLS + 1; i += 1) {
      const step = scriptedAction(j, obs([]), state);
      state = step.state;
      action = step.action;
    }
    expect(action.verb).toBe("give_up");
    expect((action as { summary: string }).summary).toContain("永远不出现");
  });

  it("写坏的正则当成「找不到」处理，不抛", () => {
    const broken = journey([{ want: "([unclosed", do: "click" }]);
    expect(() => scriptedAction(broken, obs([{ name: "任意" }]), INITIAL_STATE)).not.toThrow();
    expect(scriptedAction(broken, obs([{ name: "任意" }]), INITIAL_STATE).action.verb).toBe("wait");
  });

  it("换了一条 hint 之后 stall 计数重新开始", () => {
    const two = journey([{ want: "有的", do: "click" }, { want: "没有的", do: "click" }]);
    const first = scriptedAction(two, obs([{ name: "有的" }]), { cursor: 0, stalls: 2 });
    expect(first.state).toEqual({ cursor: 1, stalls: 0 });
    const second = scriptedAction(two, obs([{ name: "有的" }]), first.state);
    expect(second.state.stalls).toBe(1);
  });
});
