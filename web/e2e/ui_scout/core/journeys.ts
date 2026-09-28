// ui_scout 的行程表（CONTRACT §79.1）：一趟 = 一个用户目标 + 一条离线剧本 + 一个到达判据。
//
// 每趟同时喂两个驾驶员：
//   · 模型驾驶员只读 `goal` / `notes` / `maxSteps`——`hints` 是给离线驾驶员的剧本，不进 prompt，
//     否则等于把答案抄给它，巡检就退化成剧本回放；
//   · 离线驾驶员（scripted.ts）只读 `hints`，让同一条循环在 CI 里确定性地跑一遍。
//
// 元素名逐字来自真沙箱的一次采样（scene=initial/running/review，1440×900，zh），不是猜的。
// `benignAlerts` 是**有理由**的白名单：沙箱自身的产物与产品本来就诚实的降级说明，记 info 不记
// warn（宪法第 3 条：不把已知的环境事实报成 bug），每条必须写清为什么。

import type { Key, Page } from "./protocol";

/** scripts/demo_seed.py 的场景词表（truth = 该文件的 SCENES）。 */
export type Scene = "captured" | "initial" | "approved" | "running" | "steer" | "review" | "done";

export interface Hint {
  /** 对 `<role> <name>` 整串做不分大小写匹配的正则源码 */
  want?: string;
  do: "click" | "type" | "press" | "goto" | "wait";
  text?: string;
  key?: Key;
  page?: Page;
  ms?: number;
  /** 这一步找不到对应元素就跳过，而不是卡住 */
  optional?: boolean;
}

/** 到达判据：一趟结束时不成立就记一条 stuck（warn）。 */
export type GoalCheck =
  | { kind: "none" }
  | { kind: "selector"; selector: string }
  | { kind: "absent"; selector: string }
  | { kind: "lang"; value: string };

export interface Journey {
  name: string;
  scene: Scene;
  lang: "zh" | "en";
  viewport: { width: number; height: number };
  /** 交给驾驶员的一句话目标 */
  goal: string;
  /** 沙箱须知：不写清楚，模型会把已知的环境事实当成 bug 报回来 */
  notes: string;
  maxSteps: number;
  /** 单步预算（ms）——超了记 warn。UI 本地动作给得紧，发请求的那几趟给得松。 */
  stepBudgetMs: number;
  /**
   * 这一趟要不要独占一个沙箱。
   *
   * 同一个场景的行程默认共用一台 server（起一次要几秒）。但凡改**服务端**设置的行程都必须
   * 独占：语言开关写的是 `PUT /api/settings` 的 `general.language`，一趟改完，后面同场景的
   * 行程全部继承英文界面——首跑实测 narrow_viewport 因此报了一条英文文案的 overflow。
   * 只写 inbox 的行程（捕获/直跑/验收/恢复）不改投影，不必独占。
   */
  isolate?: boolean;
  check: GoalCheck;
  benignAlerts: { pattern: string; why: string }[];
  hints: Hint[];
}

const SANDBOX_NOTES = [
  "这是一个一次性沙箱：临时 AIASSISTANT_HOME + scripts/demo_seed.py 的全虚构数据，绝不是真数据。",
  "沙箱里**没有跑 actd**（守护进程）。所以任何写动作（批准/验收/停止/恢复/捕获）发出去之后，",
  "卡片不会换列、计数不会变——这是沙箱的已知事实，不是 bug，不要报。你要看的是：按钮按得下去吗、",
  "有没有回执、界面有没有炸、有没有出现自相矛盾的数字或被裁掉的文字。",
].join("");

/** 沙箱里三条本来就该出现的说明（都不是 bug）。 */
const SHELL_ONLY_ALERTS = [
  { pattern: "录制引擎只在看板 app", why: "浏览器里打开看板时产品本来就这么诚实地说明（§54 壳专属能力）" },
  { pattern: "字幕引擎与偏好只在看板 app", why: "同上，壳专属能力在浏览器里不可控" },
  { pattern: "doctor 没跑成", why: "沙箱的 python3 不一定带 PyYAML，doctor 子进程起不来——环境事实，不是产品缺陷" },
];

export const JOURNEYS: readonly Journey[] = [
  {
    name: "rail_walk",
    scene: "initial",
    lang: "zh",
    viewport: { width: 1440, height: 900 },
    goal: "把左栏的每一个页面都走一遍，最后回到任务台。每到一页先看清楚它渲染出来了没有。",
    notes: SANDBOX_NOTES,
    maxSteps: 14,
    stepBudgetMs: 4000,
    check: { kind: "selector", selector: ".board-columns, .board-page" },
    benignAlerts: SHELL_ONLY_ALERTS,
    hints: [
      { want: "^link 会议纪要$", do: "click" },
      { want: "^link 录制与数据接入$", do: "click" },
      { want: "^link 技能$", do: "click" },
      { want: "^link 回收站$", do: "click" },
      { want: "^link 永久性完成$", do: "click" },
      { want: "^link 设置$", do: "click" },
      { want: "^link 关于$", do: "click" },
      { want: "^link 任务台$", do: "click" },
    ],
  },
  {
    name: "board_tour",
    scene: "initial",
    lang: "zh",
    viewport: { width: 1440, height: 900 },
    goal: "在任务台上把四列看一遍，随便挑一张提案卡展开它的详情，读完再把详情关掉回到看板。",
    notes: SANDBOX_NOTES + "「展开详情 ▸」开的是一个抽屉（drawer），它盖在看板上面；关它按 Esc 或抽屉右上角的「关闭」。",
    maxSteps: 10,
    stepBudgetMs: 4000,
    check: { kind: "absent", selector: ".zai-drawer-root" },
    benignAlerts: [],
    hints: [
      { want: "展开详情", do: "click" },
      { do: "wait", ms: 600 },
      { do: "press", key: "Escape" },
      { do: "wait", ms: 300 },
    ],
  },
  {
    name: "quick_capture",
    scene: "initial",
    lang: "zh",
    viewport: { width: 1440, height: 900 },
    goal: "在提案那一列的输入框里写一句话，按「捕获」把它交出去，然后确认界面给了你回执。",
    notes: SANDBOX_NOTES,
    maxSteps: 10,
    stepBudgetMs: 8000,
    check: { kind: "selector", selector: "[data-capture-receipt], .composer-error" },
    benignAlerts: [],
    hints: [
      { want: "AI 来研究并提案", do: "type", text: "ui_scout 冒烟：给看板加一个导出按钮" },
      { want: "^button 捕获$", do: "click" },
      { do: "wait", ms: 1200 },
    ],
  },
  {
    name: "direct_run",
    scene: "initial",
    lang: "zh",
    viewport: { width: 1440, height: 900 },
    goal: "在运行中那一列的直跑框里写一句话，按「直跑」交出去，然后确认界面给了你回执。",
    notes: SANDBOX_NOTES,
    maxSteps: 10,
    stepBudgetMs: 8000,
    check: { kind: "selector", selector: "[data-capture-receipt], .composer-error" },
    benignAlerts: [],
    hints: [
      { want: "直接开跑", do: "type", text: "ui_scout 冒烟：把 README 的第一段重写一遍" },
      { want: "^button 直跑$", do: "click" },
      { do: "wait", ms: 1200 },
    ],
  },
  {
    name: "running_stop",
    scene: "running",
    lang: "zh",
    viewport: { width: 1440, height: 900 },
    goal: "在运行中那一列挑一张卡按「停止」，看清楚弹出来的确认框问了什么，然后取消掉、别真停。",
    notes: SANDBOX_NOTES,
    maxSteps: 10,
    stepBudgetMs: 6000,
    check: { kind: "absent", selector: "dialog[open]" },
    benignAlerts: [],
    hints: [
      { want: "^button 停止$", do: "click" },
      { do: "wait", ms: 400 },
      { want: "取消|Cancel", do: "click", optional: true },
      { do: "press", key: "Escape", optional: true },
    ],
  },
  {
    name: "review_accept",
    scene: "review",
    lang: "zh",
    viewport: { width: 1440, height: 900 },
    goal: "在待验收那一列找一张卡，读 AI 评语，再按「验收」把它验收掉，确认按下之后界面有反应。",
    notes: SANDBOX_NOTES,
    maxSteps: 10,
    stepBudgetMs: 8000,
    check: { kind: "selector", selector: "section.board-column" },
    benignAlerts: [],
    hints: [
      { want: "AI 评语", do: "click", optional: true },
      { want: "^button 验收$", do: "click" },
      { do: "wait", ms: 1200 },
    ],
  },
  {
    name: "trash_restore",
    scene: "initial",
    lang: "zh",
    viewport: { width: 1440, height: 900 },
    goal: "去回收站，把里面的一张卡恢复回看板，确认按下「恢复」之后界面有反应。",
    notes: SANDBOX_NOTES,
    maxSteps: 10,
    stepBudgetMs: 8000,
    check: { kind: "selector", selector: ".trash-page" },
    benignAlerts: [],
    hints: [
      { want: "^link 回收站$", do: "click" },
      { do: "wait", ms: 400 },
      { want: "^button 恢复$", do: "click" },
      { do: "wait", ms: 1200 },
    ],
  },
  {
    name: "settings_fold",
    scene: "initial",
    lang: "zh",
    viewport: { width: 1440, height: 900 },
    goal: "去设置页，把「显示」那一区展开，看清楚里面有哪些旋钮。",
    notes: SANDBOX_NOTES,
    maxSteps: 10,
    stepBudgetMs: 6000,
    check: { kind: "selector", selector: ".settings-page" },
    benignAlerts: SHELL_ONLY_ALERTS,
    hints: [
      { want: "^link 设置$", do: "click" },
      { do: "wait", ms: 400 },
      { want: "显示$", do: "click" },
      { do: "wait", ms: 400 },
    ],
  },
  {
    name: "language_switch",
    scene: "initial",
    lang: "zh",
    viewport: { width: 1440, height: 900 },
    goal: "把界面切成英文，然后把整个外壳（左栏、页头、列头、按钮）看一遍，找还留着中文的地方。",
    notes: SANDBOX_NOTES + "切换语言的按钮在页头右上角，中文界面上写着「切换到英文」。",
    maxSteps: 10,
    stepBudgetMs: 6000,
    isolate: true,
    check: { kind: "lang", value: "en" },
    benignAlerts: [],
    hints: [
      { want: "切换到英文|Switch to Chinese", do: "click" },
      { do: "wait", ms: 800 },
    ],
  },
  {
    name: "narrow_viewport",
    scene: "initial",
    lang: "zh",
    viewport: { width: 390, height: 844 },
    goal: "在手机那么窄的窗口里把看板用一遍：页头还读得懂吗、列还能滚吗、有没有文字被挤烂。",
    notes: SANDBOX_NOTES
      + "窄屏下页头会主动收窄：搜索框折成一个按钮、过滤 chips 收进弹层——这是设计（headerDensity），不是回归。",
    maxSteps: 10,
    stepBudgetMs: 4000,
    check: { kind: "selector", selector: "section.board-column" },
    benignAlerts: [],
    hints: [
      { want: "展开详情", do: "click", optional: true },
      { do: "wait", ms: 400 },
    ],
  },
];

export function journeyByName(name: string): Journey | undefined {
  return JOURNEYS.find((journey) => journey.name === name);
}

/** 按场景分组——同一个场景只起一次 server（起一次要几秒，别浪费）。 */
export function groupByScene(journeys: readonly Journey[]): Map<Scene, Journey[]> {
  const out = new Map<Scene, Journey[]>();
  for (const journey of journeys) {
    const bucket = out.get(journey.scene);
    if (bucket) bucket.push(journey);
    else out.set(journey.scene, [journey]);
  }
  return out;
}
