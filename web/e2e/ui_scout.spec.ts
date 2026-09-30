// ui_scout —— 探索式 UI 巡检（CONTRACT §79，issue #449）。
//
// 一个驾驶员（离线剧本，或一个外部多模态模型）在真沙箱里像人一样用这个看板：点、打字、等、
// 看屏幕；判官在旁边记账。这个 spec 是它的入口，默认跑**离线**驾驶员——所以它在 CI 里是一条
// 确定性的回归测试，不花钱、不出网；换模型驾驶只是换一个环境变量。
//
//   npm run ui-scout                                                  # 离线驾驶员，全部行程
//   ZAI_UI_SCOUT_PILOT='python3 ../scripts/qa/ui_scout_pilot.py' npm run ui-scout
//   ZAI_UI_SCOUT_JOURNEYS=language_switch,board_tour npm run ui-scout
//
// 报告落 `.ui-scout/reports/<时间戳>/`（仓库根，已 gitignore，只留最近 N 次）：report.json 是
// 机器面（`python3 scripts/qa/ui_scout.py --run <dir>` 读它出判决），report.html 是人面。
//
// 判红只在**最后一条** test 上（「巡检判决」）：行程的 test 只跑、只记账，不断言——playwright
// 在一条 test 失败后会重启 worker，而 worker 一重启这份 spec 就被重新 import、报告目录换一个
// 时间戳、半份报告落在两处。把断言压到最后一条，一次巡检就永远只有一份完整报告。
import { expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { startDemoServer, type DemoServer } from "./demoServer";
import { JOURNEYS, groupByScene, type Journey } from "./ui_scout/core/journeys";
import { FAIL_SEVERITY } from "./ui_scout/core/findings";
import { DEFAULT_PILOT_TIMEOUT_MS } from "./ui_scout/core/protocol";
import { processPilot, scriptedPilot, type Pilot } from "./ui_scout/pilot";
import { buildReport, pruneRuns, runJourney, writeReport, type JourneyResult } from "./ui_scout/run";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const REPORTS_ROOT = path.join(REPO_ROOT, ".ui-scout", "reports");
const STARTED_AT = new Date().toISOString();
const RUN_DIR = path.join(REPORTS_ROOT, STARTED_AT.replace(/[:.]/g, "-"));
const STARTED_MS = Date.now();

/** 环境变量里的数字：写歪了（空串、abc、NaN）一律退回默认值，不把 NaN 传给下游。 */
function num(value: string | undefined, fallback: number): number {
  const parsed = Number(value);
  return value !== undefined && value !== "" && Number.isFinite(parsed) ? parsed : fallback;
}

const PILOT_COMMAND = process.env.ZAI_UI_SCOUT_PILOT ?? "";
const PILOT_NAME = PILOT_COMMAND ? `process:${PILOT_COMMAND}` : "scripted";
const PILOT_TIMEOUT_MS = num(process.env.ZAI_UI_SCOUT_PILOT_TIMEOUT_MS, DEFAULT_PILOT_TIMEOUT_MS);
const KEEP_RUNS = num(process.env.ZAI_UI_SCOUT_KEEP, 5);
const SCREENSHOTS = process.env.ZAI_UI_SCOUT_NO_SHOTS !== "1";
const ONLY = (process.env.ZAI_UI_SCOUT_JOURNEYS ?? "").split(",").map((name) => name.trim()).filter(Boolean);
// 成本闸（issue #449「Cap steps per journey」）：模型驾驶时一步 = 一次带图的模型往返，
// 想先花小钱看一眼就把它压到 3~4。0 / 缺席 = 用行程表自己的 maxSteps。
const MAX_STEPS = num(process.env.ZAI_UI_SCOUT_MAX_STEPS, 0);

const SELECTED = (ONLY.length ? JOURNEYS.filter((journey) => ONLY.includes(journey.name)) : JOURNEYS)
  .map((journey) => (MAX_STEPS > 0 ? { ...journey, maxSteps: Math.min(journey.maxSteps, MAX_STEPS) } : journey));
const RESULTS: JourneyResult[] = [];
const SANDBOX = { config: true, skills: true, pythonUserSite: true };

function makePilot(journey: Journey): Pilot {
  if (!PILOT_COMMAND) return scriptedPilot(journey);
  return processPilot({
    command: PILOT_COMMAND,
    timeoutMs: PILOT_TIMEOUT_MS,
    cwd: path.join(REPO_ROOT, "web"),
    // 预算只有一个真源（防腐 #9）：跑者的读超时逐字传给驾驶员，驾驶员自己的模型超时从它
    // 派生且必须严格更短——这样「没答上来」永远由驾驶员先说出口（一行 give_up），而不是
    // 跑者先放弃、模型的回答随后漂回来。
    env: {
      ZAI_UI_SCOUT_JOURNEY: journey.name,
      ZAI_UI_SCOUT_PILOT_TIMEOUT_MS: String(PILOT_TIMEOUT_MS),
    },
    onStderr: (text) => process.stderr.write(`[pilot:${journey.name}] ${text}`),
  });
}

for (const [scene, journeys] of groupByScene(SELECTED)) {
  test.describe(`scene ${scene}`, () => {
    let server: DemoServer;

    const shared = journeys.some((journey) => !journey.isolate);

    test.beforeAll(async () => {
      // demoServer 最多等 90 s（macOS runner 的 getfqdn 反查），hook 超时要给得比它宽
      test.setTimeout(150_000);
      if (shared) server = await startDemoServer(scene, SANDBOX);
    });

    test.afterAll(() => server?.stop());

    for (const journey of journeys) {
      test(journey.name, async ({ page }) => {
        // 离线驾驶员一趟几秒；模型驾驶时一步就是一次带图的模型往返（本机实测 12~35 s/步，
        // 随模型与这一屏的复杂度浮动），所以预算按「步数 × 单步超时」算——按均值算会在最慢的
        // 那一步上翻车。再加一分钟给起浏览器与截图。
        test.setTimeout(PILOT_COMMAND ? journey.maxSteps * PILOT_TIMEOUT_MS + 60_000 : 240_000);
        // 改服务端设置的行程独占一台 server，免得把后面同场景的行程带偏（见 Journey.isolate）
        const own = journey.isolate ? await startDemoServer(scene, SANDBOX) : null;
        const pilot = makePilot(journey);
        try {
          RESULTS.push(await runJourney(page, journey, pilot, {
            baseURL: (own ?? server).baseURL, runDir: RUN_DIR, screenshots: SCREENSHOTS,
          }));
        } finally {
          pilot.close();
          own?.stop();
        }
      });
    }
  });
}

test("巡检判决", () => {
  const report = buildReport(PILOT_NAME, STARTED_AT, Date.now() - STARTED_MS, RESULTS);
  writeReport(report, RUN_DIR);
  pruneRuns(REPORTS_ROOT, KEEP_RUNS);
  const { journeys, steps, error, warn, info } = report.counts;
  process.stdout.write(`\nui_scout: ${journeys} 趟 / ${steps} 步 — error=${error} warn=${warn} info=${info}`
    + `\n  ${path.join(RUN_DIR, "report.html")}\n`);

  expect(RESULTS.length, "一趟行程都没跑起来——沙箱或行程表出了问题").toBe(SELECTED.length);
  const errors = report.findings
    .filter((finding) => finding.severity === FAIL_SEVERITY)
    .map((finding) => `${finding.journey} / ${finding.oracle}: ${finding.summary}`);
  expect(errors, `确定性判官报了 error（报告 ${path.join(RUN_DIR, "report.html")}）`).toEqual([]);
});
