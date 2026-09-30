// 两个驾驶员的统一面（CONTRACT §79.2）。
//
//   · scripted —— 离线、确定性、零成本，CI 跑的就是它；
//   · process  —— 把一步交给外部命令：stdin 收一行 Observation JSON，stdout 回一行 Action JSON。
//     模型无关，所以 Claude / Kimi K3 / 任何多模态端点都只要满足这一页；参考实现是
//     `python3 scripts/qa/ui_scout_pilot.py`（走 act/llm.py 那唯一的 LLM 边界）。
//
// 进程驾驶员的四条纪律：一趟一个进程（上下文不跨行程串味）、一步一个超时（模型卡住不许
// 拖垮巡检）、一问一答严格配对（超时后漂回来的那一行要么被 drain 掉、要么被 step 回声闸
// 拒掉，绝不拿来答下一问）、任何异常都降级成一条 `{ok:false}` 而不是抛（宪法第 11 条）。
import { spawn, type ChildProcess } from "node:child_process";
import type { ActionParse, Observation } from "./core/protocol";
import { parseActionLine } from "./core/protocol";
import { scriptedAction, INITIAL_STATE, type ScriptedState } from "./core/scripted";
import type { Journey } from "./core/journeys";

export interface Pilot {
  name: string;
  next(obs: Observation): Promise<ActionParse>;
  close(): void;
}

export function scriptedPilot(journey: Journey): Pilot {
  let state: ScriptedState = { ...INITIAL_STATE };
  return {
    name: "scripted",
    async next(obs: Observation): Promise<ActionParse> {
      const step = scriptedAction(journey, obs, state);
      state = step.state;
      return { ok: true, action: step.action };
    },
    close() { /* 没有进程可关 */ },
  };
}

/** 一行一答的读取器：攒 stdout，攒到换行就交一行出去。 */
function lineReader(child: ChildProcess) {
  let buffer = "";
  const pending: string[] = [];
  let resolve: ((line: string) => void) | null = null;
  child.stdout?.on("data", (chunk) => {
    buffer += String(chunk);
    let index = buffer.indexOf("\n");
    while (index >= 0) {
      const line = buffer.slice(0, index);
      buffer = buffer.slice(index + 1);
      if (resolve) { const r = resolve; resolve = null; r(line); }
      else pending.push(line);
      index = buffer.indexOf("\n");
    }
  });
  return {
    /**
     * 丢掉所有还没被领走的行，问下一问之前先清台面。
     * 上一步超时之后模型那一行还是会漂回来——不清就会被当成下一问的答案（§79.2 的配对
     * 闸会拒，但让它根本不进门更省事）。返回丢了几行，好让上层照实说出来。
     */
    drain(): number {
      // 写到一半的那半行也算一条——丢了就要说，不许悄悄扔（宪法第 3 条）。
      const dropped = pending.length + (buffer ? 1 : 0);
      pending.length = 0;
      buffer = "";
      return dropped;
    },
    read(timeoutMs: number): Promise<string | null> {
      const ready = pending.shift();
      if (ready !== undefined) return Promise.resolve(ready);
      return new Promise((done) => {
        const timer = setTimeout(() => { resolve = null; done(null); }, timeoutMs);
        resolve = (line: string) => { clearTimeout(timer); done(line); };
      });
    },
  };
}

export interface ProcessPilotOptions {
  command: string;
  timeoutMs: number;
  env?: Record<string, string>;
  cwd?: string;
  /** 驾驶员写到 stderr 的东西原样转出来——它自己的诊断不该被吞掉 */
  onStderr?: (text: string) => void;
}

export function processPilot(options: ProcessPilotOptions): Pilot {
  const child = spawn(options.command, {
    shell: true,
    cwd: options.cwd,
    env: { ...process.env, ...(options.env ?? {}) },
    stdio: ["pipe", "pipe", "pipe"],
  });
  const reader = lineReader(child);
  let dead: string | null = null;
  child.on("error", (error) => { dead = String(error); });
  child.on("exit", (code) => { dead = dead ?? `驾驶员进程退出了（code=${code}）`; });
  child.stderr?.on("data", (chunk) => options.onStderr?.(String(chunk)));

  return {
    name: `process:${options.command}`,
    async next(obs: Observation): Promise<ActionParse> {
      if (dead) return { ok: false, reason: dead };
      const stale = reader.drain();
      if (stale) options.onStderr?.(`（丢弃了 ${stale} 行迟到的回答——上一步超时后才漂回来的）\n`);
      try {
        child.stdin?.write(`${JSON.stringify(obs)}\n`);
      } catch (error) {
        return { ok: false, reason: `写不进驾驶员的 stdin：${String(error)}` };
      }
      const line = await reader.read(options.timeoutMs);
      if (line === null) {
        return { ok: false, reason: `驾驶员 ${options.timeoutMs}ms 内没有回答` };
      }
      return parseActionLine(line, obs.elements.map((element) => element.ref), obs.step);
    },
    close() {
      try { child.stdin?.end(); } catch { /* 关就是了 */ }
      child.kill();
    },
  };
}
