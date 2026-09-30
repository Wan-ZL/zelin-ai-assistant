// 离线驾驶员（CONTRACT §79.2 的第二个 pilot）：同一条循环，确定性的那一版。
//
// 为什么要它：巡检的价值在循环本身（观察 → 决策 → 执行 → 判官），而循环必须能在 CI 上
// 每天跑、不花钱、不出网、不看运气。离线驾驶员按行程表里的 `hints` 走，所以每次都走同一条
// 路——它是 CI 里的回归测试；模型驾驶员是本地/夜间的探索者。两者共用一份协议与一份判官，
// 所以「离线绿了、模型驾驶时炸了」只可能是真的探索到了新地方。

import type { Action } from "./protocol";
import type { Hint, Journey } from "./journeys";
import type { Observation } from "./protocol";

export interface ScriptedState {
  cursor: number;
  stalls: number;
}

/** 同一条 hint 等不到元素的最大次数；超了就认栽，不空转到 maxSteps。 */
export const MAX_STALLS = 3;

export const INITIAL_STATE: ScriptedState = { cursor: 0, stalls: 0 };

function matcher(want: string): RegExp | null {
  try {
    return new RegExp(want, "i");
  } catch {
    return null;
  }
}

function findRef(obs: Observation, hint: Hint): string | null {
  if (!hint.want) return null;
  const re = matcher(hint.want);
  if (!re) return null;
  for (const element of obs.elements) {
    if (!element.enabled) continue;
    if (hint.do === "type" && !element.editable) continue;
    if (re.test(`${element.role} ${element.name}`)) return element.ref;
  }
  return null;
}

function fromHint(hint: Hint, ref: string | null): Action | null {
  switch (hint.do) {
    case "wait": return { verb: "wait", ms: hint.ms ?? 300 };
    case "press": return hint.key ? { verb: "press", key: hint.key } : null;
    case "goto": return hint.page === undefined ? null : { verb: "goto", page: hint.page };
    case "click": return ref ? { verb: "click", ref } : null;
    case "type": return ref ? { verb: "type", ref, text: hint.text ?? "" } : null;
    default: return null;
  }
}

export interface ScriptedStep {
  action: Action;
  state: ScriptedState;
}

/**
 * 下一步：走到第一条**做得成**的 hint。
 *
 * - 不需要指认元素的 hint（wait / press / goto）直接发；
 * - 需要指认元素的，找得到就发、cursor 前进；
 * - 找不到且标了 optional —— 跳过它，继续往后找（不浪费一步）；
 * - 找不到又不能跳 —— 先等一下再试，连着 MAX_STALLS 次还没有就 give_up
 *   （行程表写错了、或者界面真的变了，两种都该留痕而不是空转到步数上限）。
 */
export function scriptedAction(journey: Journey, obs: Observation, state: ScriptedState): ScriptedStep {
  let cursor = state.cursor;
  while (cursor < journey.hints.length) {
    const hint = journey.hints[cursor];
    const ref = hint.want ? findRef(obs, hint) : null;
    const action = fromHint(hint, ref);
    if (action) {
      return { action, state: { cursor: cursor + 1, stalls: 0 } };
    }
    if (hint.optional) {
      cursor += 1;
      continue;
    }
    const stalls = state.cursor === cursor ? state.stalls + 1 : 1;
    if (stalls >= MAX_STALLS) {
      return {
        action: { verb: "give_up", summary: `行程表第 ${cursor + 1} 步等不到「${hint.want ?? hint.do}」` },
        state: { cursor, stalls },
      };
    }
    return { action: { verb: "wait", ms: 400 }, state: { cursor, stalls } };
  }
  return { action: { verb: "done", summary: "剧本走完" }, state: { cursor, stalls: 0 } };
}
