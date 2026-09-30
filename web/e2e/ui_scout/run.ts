// ui_scout 的循环（CONTRACT §79.2）：观察 → 决策 → 执行 → 判官，一趟走到目标或走到步数上限。
//
// 这一层是胶水：起浏览器、截图、落报告。判决口径全在 core/（纯函数，判例喂字面量就能钉）。
// 两条红线写在这里：
//   · 驾驶员只能按 ref 指认元素，selector 由本文件按 `data-ui-scout-ref` 拼；
//   · 模型自己说的「我觉得这里不对」一律记 info——它是线索不是判决（§79.4）。
import { mkdirSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { REF_ATTR, probe, settling, type Probe } from "./observe";
import { PROTOCOL_VERSION, describeAction, type Action, type Observation } from "./core/protocol";
import { toFinding, dedupe, type Finding } from "./core/findings";
import type { OracleHit } from "./core/findings";
import {
  alertHits, budgetHits, consoleHits, crashHits, i18nHits, laneCountHits, overflowHits,
  projectionHits, type BoardData, type StepTiming,
} from "./core/oracles";
import type { GoalCheck, Journey } from "./core/journeys";
import { renderReportHtml, summarize, type JourneyRecord, type ScoutReport, type StepRecord } from "./core/report";
import type { Pilot } from "./pilot";

export interface RunContext {
  baseURL: string;
  /** 本次巡检的落点目录（绝对路径）；截图写成 <journey>/step-NN.png */
  runDir: string;
  screenshots: boolean;
}

const ACTION_TIMEOUT_MS = 10_000;
// 沙箱里没有 actd，发出去的动作永远不会「落定」（卡面的「处理中」小字一直挂着），所以等落定
// 必须有上限，而且要短——这 1.5 s 是每一步的固定代价，不是异常。
const SETTLE_TIMEOUT_MS = 1_500;

function refSelector(ref: string): string {
  return `[${REF_ATTR}="${ref}"]`;
}

/** `/api/board` 的两份计数（counts 字典 + 每个分区数组长度）。取不到就返回 null，不瞎判。 */
export async function boardData(baseURL: string): Promise<BoardData | null> {
  try {
    const response = await fetch(`${baseURL}/api/board`);
    if (!response.ok) return null;
    const payload = await response.json() as Record<string, unknown>;
    const counts = (payload.counts ?? {}) as Record<string, number>;
    const lengths: Record<string, number> = {};
    for (const [key, value] of Object.entries(payload)) {
      if (Array.isArray(value)) lengths[key] = value.length;
    }
    return { counts, lengths };
  } catch {
    return null;
  }
}

/** 等这一帧落定：spinner / 「处理中」小字都没了才算。等不到也不报错，交给判官去说。 */
async function settle(page: any): Promise<void> {
  const deadline = Date.now() + SETTLE_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!(await settling(page))) return;
    await page.waitForTimeout(150);
  }
}

async function applyAction(page: any, baseURL: string, action: Action): Promise<void> {
  switch (action.verb) {
    case "click":
      await page.locator(refSelector(action.ref)).first().click({ timeout: ACTION_TIMEOUT_MS });
      return;
    case "type":
      await page.locator(refSelector(action.ref)).first().fill(action.text, { timeout: ACTION_TIMEOUT_MS });
      return;
    case "press":
      await page.keyboard.press(action.key);
      return;
    case "wait":
      await page.waitForTimeout(action.ms);
      return;
    case "goto":
      await page.goto(action.page ? `${baseURL}/?page=${action.page}` : `${baseURL}/`);
      return;
    default:
      return;
  }
}

function benign(journey: Journey, hit: OracleHit): boolean {
  if (hit.oracle !== "alert") return false;
  return journey.benignAlerts.some((entry) => hit.detail.includes(entry.pattern));
}

/** 判官跑一遍：这一帧 + 这一步的数据。`benignAlerts` 命中的降级成 info，不是丢掉。 */
export function oracleHits(journey: Journey, snapshot: Probe, board: BoardData | null,
                           consoleMessages: readonly string[]): OracleHit[] {
  const hits: OracleHit[] = [
    ...crashHits(snapshot.crash),
    ...consoleHits(consoleMessages),
    ...alertHits(snapshot.alerts),
    ...overflowHits(snapshot.boxes),
    ...i18nHits(snapshot.chrome, snapshot.lang.slice(0, 2)),
  ];
  if (board) {
    hits.push(...projectionHits(board), ...laneCountHits(snapshot.lanes, board));
  }
  return hits.map((hit) => (benign(journey, hit)
    ? { ...hit, severity: "info" as const, detail: `${hit.detail}（行程表登记的已知沙箱事实）` }
    : hit));
}

async function checkReached(page: any, check: GoalCheck): Promise<boolean> {
  switch (check.kind) {
    case "none": return true;
    case "lang": return (await page.evaluate(() => document.documentElement.lang)).startsWith(check.value);
    case "selector": return (await page.locator(check.selector).count()) > 0;
    case "absent": return (await page.locator(check.selector).count()) === 0;
    default: return true;
  }
}

export interface JourneyResult {
  record: JourneyRecord;
  findings: Finding[];
}

export async function runJourney(page: any, journey: Journey, pilot: Pilot,
                                 ctx: RunContext): Promise<JourneyResult> {
  const shotDir = path.join(ctx.runDir, journey.name);
  if (ctx.screenshots) mkdirSync(shotDir, { recursive: true });

  const consoleMessages: string[] = [];
  page.on("console", (message: any) => {
    if (message.type() === "error") consoleMessages.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error: any) => consoleMessages.push(`pageerror: ${error.message}`));

  await page.addInitScript((lang: string) => {
    window.localStorage.setItem("zai.theme", "light");
    window.localStorage.setItem("zai.lang", lang);
    window.localStorage.setItem("boardAnimations", "false");
  }, journey.lang);
  await page.setViewportSize(journey.viewport);
  await page.goto(`${ctx.baseURL}/`);
  await settle(page);

  const steps: StepRecord[] = [];
  const findings: Finding[] = [];
  const timings: StepTiming[] = [];
  let outcome: JourneyRecord["outcome"] = "out_of_steps";

  for (let step = 1; step <= journey.maxSteps; step += 1) {
    const before = await probe(page);
    const shot = ctx.screenshots ? path.join(journey.name, `step-${String(step).padStart(2, "0")}.png`) : null;
    if (shot) await page.screenshot({ path: path.join(ctx.runDir, shot) });

    const observation: Observation = {
      protocol: PROTOCOL_VERSION,
      journey: journey.name,
      goal: journey.goal,
      notes: journey.notes,
      step,
      maxSteps: journey.maxSteps,
      url: before.url,
      page: before.page,
      lang: before.lang,
      viewport: journey.viewport,
      screenshot: shot ? path.join(ctx.runDir, shot) : "",
      elements: before.elements,
      text: before.text,
      lastAction: steps.length ? steps[steps.length - 1].action : null,
      lastError: steps.length ? steps[steps.length - 1].error : null,
      settling: before.settling,
    };

    const askedAt = Date.now();
    const parsed = await pilot.next(observation);
    const pilotMs = Date.now() - askedAt;
    if (!parsed.ok) {
      const record: StepRecord = {
        step, action: "(驾驶员没给出可执行的动作)", ok: false, error: parsed.reason,
        ms: 0, pilotMs, url: before.url, page: before.page, screenshot: shot,
      };
      steps.push(record);
      findings.push(toFinding({
        oracle: "pilot", severity: "warn",
        summary: `驾驶员第 ${step} 步答不上来：${parsed.reason}`,
        detail: parsed.reason, signature: `pilot ${parsed.reason}`,
      }, { journey: journey.name, step, url: before.url, screenshot: shot }));
      continue;
    }

    const action = parsed.action;
    const label = describeAction(action);
    let error: string | null = null;
    // 时间分两笔记（§79.3）：`ms` 只量**界面**的往返，驾驶员想多久单独记 `pilotMs`——
    // 预算判官吃的是前者，否则模型驾驶时每一步都会因为模型慢而「超预算」，判出一屏假黄。
    const actedAt = Date.now();
    try {
      await applyAction(page, ctx.baseURL, action);
      await settle(page);
    } catch (thrown) {
      error = String(thrown).slice(0, 400);
    }
    const ms = Date.now() - actedAt;
    steps.push({ step, action: label, ok: error === null, error, ms, pilotMs,
                 url: before.url, page: before.page, screenshot: shot });
    timings.push({ step, action: label, ms });

    if (error !== null) {
      findings.push(toFinding({
        oracle: "action", severity: "error",
        summary: `第 ${step} 步做不下去：${label}`,
        detail: error, signature: `action ${label.split(" ")[0]} ${error}`,
      }, { journey: journey.name, step, url: before.url, screenshot: shot }));
    }

    // 模型自己说的「这里不对」：记下来，但永远只是 info——它是线索不是判决（§79.4）。
    if (action.verb === "report") {
      findings.push(toFinding({
        oracle: "pilot", severity: "info",
        summary: action.summary,
        detail: `驾驶员自报严重度 ${action.severity}：${action.detail}`,
        signature: `pilot report ${action.summary}`,
      }, { journey: journey.name, step, url: before.url, screenshot: shot }));
    }

    const after = await probe(page);
    const drained = consoleMessages.splice(0, consoleMessages.length);
    const board = await boardData(ctx.baseURL);
    const ctxFinding = { journey: journey.name, step, url: after.url, screenshot: shot };
    for (const hit of oracleHits(journey, after, board, drained)) {
      findings.push(toFinding(hit, ctxFinding));
    }

    if (action.verb === "done") { outcome = "done"; break; }
    if (action.verb === "give_up") { outcome = "gave_up"; break; }
  }

  // 预算判官回头看整张时间表，每条 hit 自带它说的那一步（OracleHit.step）。
  for (const hit of budgetHits(timings, journey.stepBudgetMs)) {
    const at = steps.find((record) => record.step === hit.step);
    findings.push(toFinding(hit, {
      journey: journey.name, step: hit.step ?? steps.length,
      url: at?.url ?? ctx.baseURL, screenshot: at?.screenshot ?? null,
    }));
  }

  const reached = await checkReached(page, journey.check);
  if (!reached) {
    findings.push(toFinding({
      oracle: "stuck", severity: "warn",
      summary: `没走到目标：${journey.goal}`,
      detail: `到达判据 ${JSON.stringify(journey.check)} 在这一趟结束时不成立。`,
      signature: `stuck ${journey.name}`,
    }, { journey: journey.name, step: steps.length, url: ctx.baseURL, screenshot: null }));
  }

  const result: JourneyResult = {
    record: {
      name: journey.name, scene: journey.scene, lang: journey.lang, goal: journey.goal,
      viewport: journey.viewport, outcome, reached, steps,
    },
    findings: dedupe(findings),
  };
  // 一趟一落盘：整份报告在最后才拼，但半路崩掉时已经走过的行程不该跟着蒸发。
  try {
    mkdirSync(shotDir, { recursive: true });
    writeFileSync(path.join(shotDir, "result.json"), `${JSON.stringify(result, null, 2)}\n`, "utf-8");
  } catch { /* 落盘失败不该把巡检带崩（宪法第 11 条） */ }
  return result;
}

export function buildReport(pilotName: string, startedAt: string, durationMs: number,
                            results: readonly JourneyResult[]): ScoutReport {
  const journeys = results.map((result) => result.record);
  const findings = dedupe(results.flatMap((result) => result.findings));
  return {
    protocol: PROTOCOL_VERSION,
    pilot: pilotName,
    startedAt,
    durationMs,
    journeys,
    findings,
    counts: summarize(journeys, findings),
  };
}

export function writeReport(report: ScoutReport, runDir: string): void {
  mkdirSync(runDir, { recursive: true });
  writeFileSync(path.join(runDir, "report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf-8");
  writeFileSync(path.join(runDir, "report.html"), renderReportHtml(report), "utf-8");
}

/**
 * 留痕但不无限长（防腐 #4：append-only 的东西出生当天就带保留期）。
 * 只删 `<root>` 下的目录，只按名字排序留最近 `keep` 个。
 */
export function pruneRuns(root: string, keep: number): string[] {
  if (!Number.isFinite(keep) || keep < 1) return [];   // 认不出的保留期 = 一个都不删
  let entries: string[];
  try {
    entries = readdirSync(root).filter((name) => {
      try { return statSync(path.join(root, name)).isDirectory(); } catch { return false; }
    }).sort();
  } catch {
    return [];
  }
  const doomed = entries.slice(0, Math.max(entries.length - keep, 0));
  for (const name of doomed) rmSync(path.join(root, name), { recursive: true, force: true });
  return doomed;
}
