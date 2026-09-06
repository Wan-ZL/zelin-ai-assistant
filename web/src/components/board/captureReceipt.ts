// 列顶输入框（提案列「捕获」/ 运行中列「直跑」）成功回执的诚实纪律（CONTRACT §10 / §41 2026-09-05 追记；
// 原生 mac/Sources/Cards.swift:934,951-956 processingBody · :848,863-867 RunCapturePendingRow ·
// Store.swift:343-353 sweepTimeouts · :402-411 超时文案 · :652-659 updateHealth 重新起算 · PendingSweep.swift:169-192
// captureMatches）。纯函数、零 React：只看 (输入的原文 × 管线健康 × 当前看板快照)。
//
// 原生的乐观回显是一张本地灰色占位卡（提案列 / 运行中列顶），web **不搬那张卡**：server 的 processing / queued 行
// 在一个 actd pass（默认 10 s）内就落进列里（act/lib/actd/inbox.py `_capture_proposal` 置 raising、`_capture_direct_run`
// 置 approved，act/actd.py run_once 在 n_inbox>0 时 `_early_dashboard`），列的组成是 server 数据不是 client 代码
// （防腐 #10）。web 只保留输入框下那**一行回执**，但让它说真话：
//   - 文案随管线健康切换（原生 P1-4 `stalled = … && app.store.pipelineHealth != .ok`）——管线不 ok 时「AI 分析中
//     （通常 2-3 分钟）」是一句兑现不了的承诺，改说捕获实际在哪（已保存到队列）；判据用横幅的同一谓词
//     describeHealth（stalled / failing / stale 说话；ok / unknown 静默——原生 PipelineHealth 枚举没有 unknown，
//     把「老 daemon 还在写看板」判成 stalled 会与横幅的沉默自相矛盾）；
//   - 回执带原话前 20 字（原生占位卡的正文就是原话），并**活过键击**：它说的是上一次提交、不因新草稿开打而过期；
//   - 回执的寿命 = 原生占位卡的寿命：刷新带来一行**属于这次提交**的卡即清——先认 §10 issue #7 的精确键
//     （行的 `capture_id` = POST /api/actions 回的 `file` stem，§49；「web 若加 optimistic echo 以此键对账」），
//     再退到原生 captureMatches 的标题 / 摘要前缀猜测（归一化 + 前 10 字双向 contains）——精确键只在新铸的卡上
//     （出生行的 stem；并入已有卡时那张卡带的是它自己出生那次的 stem，直跑的 queued 行今日还不带）。
//     propose 只对 needs_approval，run 只对 running + needs_input——**刻意不对 review 清**：命中旧待验收卡时 actd ack
//     的是 noop，清了就是 fake launch；否则 300 s（propose，分析可以很慢）/ 180 s（run，无 LLM、下一轮就该排队）
//     后换成原生的诚实超时条——只在管线 ok 时计时，管线恢复时重新起算整段窗口（原生 updateHealth 重置 created）。
import type { Board, HealthSnapshot } from "../../types";
import { describeHealth } from "../shell/PipelineBanner";

export type CaptureMode = "propose" | "run";

/** 原生 Store.sweepTimeouts：提案捕获 300 s（分析可以很慢）/ 直跑 180 s（无 LLM，actd 下一轮 ~10 s 就该排队） */
export const CAPTURE_TIMEOUT_MS: Record<CaptureMode, number> = { propose: 300_000, run: 180_000 };

/** 原生 LocalNotice：超时条 120 s 自然褪去 */
export const CAPTURE_NOTICE_FADE_MS = 120_000;

/** 原生 `app.store.pipelineHealth != .ok` 的 web 版——横幅的同一谓词（stalled / failing / stale 为真；ok / unknown /
 *  还没拉到 health 为假） */
export function pipelineStalled(health: HealthSnapshot | null): boolean {
  return health ? describeHealth(health, (zh) => zh) !== null : false;
}

/** 原话前 20 个字（code point 计；原生 `String(c.text.prefix(20))`，不加省略号） */
export function clip20(s: string): string {
  return [...s].slice(0, 20).join("");
}

/** 回执的状态句（原生 Cards.swift:951-956 / :863-867 四句逐字） */
export function captureNote(mode: CaptureMode, stalled: boolean, text: (zh: string, en: string) => string): string {
  if (mode === "run") {
    return stalled
      ? text("已保存到队列，pipeline 启动后直接开跑", "Saved to the queue — runs once the pipeline is up")
      : text("已提交，直接开跑（跳过提案），排队派发中…", "Submitted — running it now (skipped proposal), queued for dispatch…");
  }
  return stalled
    ? text("已保存到队列，pipeline 启动后开始处理", "Saved to the queue — processed once the pipeline is running")
    : text("已提交，AI 分析中（通常 2-3 分钟）", "Submitted — analyzing (usually 2-3 min)");
}

/** 输入框下的一行回执：「原话前 20 字」+ 状态句 */
export function captureReceiptLine(mode: CaptureMode, typed: string, stalled: boolean, text: (zh: string, en: string) => string): string {
  const head = clip20(typed);
  return text(`「${head}」${captureNote(mode, stalled, text)}`, `"${head}" ${captureNote(mode, stalled, text)}`);
}

/** 超时条（原生 Store.swift:402-411 逐字）：直跑 180 s 没排上 = 任务真的没开始（橙）；提案捕获 300 s 多半只是分析慢（黄） */
export function captureTimeoutNotice(mode: CaptureMode, typed: string, text: (zh: string, en: string) => string): string {
  if (mode === "run") {
    const head = clip20(typed);
    return text(`「${head}」任务没有开始——后台可能没在跑（检查 actd）`, `"${head}" did not start — the backend may not be running (check actd)`);
  }
  return text(
    "分析比平时慢，卡片稍后会自动出现；一直没有就打开「依赖检查」页并查看 state/actd.log",
    "Analysis is slower than usual — the card should still appear; if it never does, open the Dependencies page and check state/actd.log",
  );
}

/** 原生 PendingSweep.normalized：小写 + 去掉空白 / 标点 / 符号——后端的引号、破折号、空格改写不破坏匹配 */
export function normalizedCapture(s: string): string {
  return s.toLowerCase().replace(/[\s\p{P}\p{S}]/gu, "");
}

const MATCH_PREFIX = 10; // 原生 `p.prefix(10)`

function field(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/** POST /api/actions 响应里的 inbox 文件名 `capture-<uuid>.json` → stem（§49；形状不对 → null，只剩前缀猜测） */
export function captureStem(response: unknown): string | null {
  const file = response && typeof response === "object" ? (response as { file?: unknown }).file : null;
  return typeof file === "string" && file.endsWith(".json") && file.length > 5 ? file.slice(0, -5) : null;
}

/** 这次提交的对账凭据：原话（前缀猜测用）+ inbox stem（精确键用；server 没回就 null） */
export interface CaptureIdentity {
  text: string;
  stem: string | null;
}

/** 这次提交落地了吗。行集合按 mode 取：propose = needs_approval（title / summary），run = running + needs_input
 *  （name / summary）；两者都不看 review——一周前的待验收卡同词会把回执清成假的「已开跑」。
 *  先认精确键 `row.capture_id === stem`（§10 issue #7），再退到原生 PendingSweep.captureMatches：归一化后前 10 字双向 contains。 */
export function captureLanded(identity: CaptureIdentity, mode: CaptureMode, board: Board): boolean {
  const rows: Array<Record<string, unknown>> = mode === "run"
    ? [...(board.running ?? []), ...(board.needs_input ?? [])]
    : (board.needs_approval ?? []);
  if (identity.stem && rows.some((r) => r.capture_id === identity.stem)) return true;
  const p = normalizedCapture(identity.text);
  if (!p) return false;
  const pKey = [...p].slice(0, MATCH_PREFIX).join("");
  const fields = mode === "run" ? ["name", "summary"] : ["title", "summary"];
  for (const row of rows) {
    for (const key of fields) {
      const t = normalizedCapture(field(row[key]));
      if (!t) continue;
      const tKey = [...t].slice(0, MATCH_PREFIX).join("");
      if (t.includes(pKey) || p.includes(tKey)) return true;
    }
  }
  return false;
}
