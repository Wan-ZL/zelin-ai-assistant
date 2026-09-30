// ui_scout 的判官（CONTRACT §79.3）——**确定性**的那一半。
//
// 模型负责「操作」与「觉得哪里不对」，但一条发现算不算 error，由这里的纯函数判：输入是
// 浏览器那边采下来的可序列化快照，输出是 OracleHit。纯函数 = 判例喂字面量就能钉死口径，
// 不必起浏览器（测试位置纪律：unit 层禁真 IO）。
//
// 七只判官：
//   crash      React 渲染炸了（AppErrorBoundary 接住）                → error
//   console    页面报错（console.error / pageerror）                  → error
//   lane_count 列头那个数字与 /api/board 的数据对不上（#449 点名那类）→ error
//   alert      这一步有东西在朝用户喊（横幅 / 卡面报错 / 输入框报错）  → warn
//   budget     单步超时间预算                                          → warn
//   overflow   文字被容器裁掉                                          → warn
//   i18n       英文界面的 chrome 里漏出中文                            → warn

import type { OracleHit } from "./findings";

/** 一列的快照：slug + 列头徽章原文 + 列里渲染出的卡数。 */
export interface LaneSnapshot {
  slug: string;
  badge: string;
  domCards: number;
}

/** `GET /api/board`（= `state/dashboard.json` 原样透传，server/board_source.py）的两份计数。 */
export interface BoardData {
  /** 投影自报的 `counts` 字典（act/lib/dashboard.py 算一次写进去） */
  counts: Record<string, number>;
  /** 每个分区数组的真实长度 */
  lengths: Record<string, number>;
}

export interface TextBox {
  where: string;
  text: string;
  scrollWidth: number;
  clientWidth: number;
}

export interface ChromeString {
  where: string;
  text: string;
}

export interface AlertSnapshot {
  where: string;
  text: string;
}

export interface StepTiming {
  step: number;
  action: string;
  ms: number;
}

/**
 * 看板四列与它们背后的 wire 字段（truth = web/src/components/board/BoardLanes.tsx 的装配）。
 *
 * 两条必须照抄的口径，抄错就是满屏假红：
 *   · 「运行中」一列同时装 `running` 与 `needs_input`，徽章是两者之和；
 *   · `completed` / `archived` 的 `counts` 是**截断前**的总数（act/lib/dashboard.py 的
 *     completed_total / archived_total），所以 `counts > 数组长度` 是正常的，不是 bug。
 * 左右两条书立（潜在任务 / 永久性完成）收起时内容不挂载、徽章算法也另一套，不在判官范围内。
 */
/**
 * 投影里**截断**的分区——`counts` 是截断前的总数，所以数组长度只会更短。
 * 这是「哪些分区会截断」的**唯一**一处；列的截断与否从它派生，不另写一份（防腐 #9）。
 */
export const CAPPED_PARTITIONS: ReadonlySet<string> = new Set(["completed", "archived"]);

export interface LaneSpec {
  slug: string;
  dataKeys: string[];
}

export const BOARD_LANES: readonly LaneSpec[] = [
  { slug: "needs_approval", dataKeys: ["needs_approval"] },
  { slug: "running", dataKeys: ["running", "needs_input"] },
  { slug: "review", dataKeys: ["review"] },
  { slug: "completed", dataKeys: ["completed"] },
];

/** 一列只要装了截断的分区，它的徽章就只允许多不允许少。 */
export function laneIsCapped(spec: LaneSpec): boolean {
  return spec.dataKeys.some((key) => CAPPED_PARTITIONS.has(key));
}

const LANE_BY_SLUG = new Map(BOARD_LANES.map((lane) => [lane.slug, lane]));

/**
 * 列头徽章原文 → 总数。
 * 两种格式（BoardLanes.tsx 的 `label(shown,total)`）：`"7"`，或过滤生效时的 `"3/7"`。
 * 认不出来返回 null —— 判官宁可不判，也不拿猜出来的数字报红（宪法第 3 条）。
 */
export function parseBadgeTotal(badge: string): number | null {
  const tail = badge.includes("/") ? badge.slice(badge.lastIndexOf("/") + 1) : badge;
  const digits = tail.trim().match(/^\d+$/);
  return digits ? Number(digits[0]) : null;
}

function expectedCards(lane: LaneSpec, board: BoardData): number | null {
  let total = 0;
  for (const key of lane.dataKeys) {
    const length = board.lengths[key];
    if (typeof length !== "number") return null;
    total += length;
  }
  return total;
}

export function crashHits(present: boolean): OracleHit[] {
  if (!present) return [];
  return [{
    oracle: "crash",
    severity: "error",
    summary: "渲染炸了：AppErrorBoundary 接管了整页",
    detail: "页面上出现了 [data-testid=\"app-error-boundary\"]——某个组件在渲染期抛了异常。",
    signature: "app-error-boundary",
  }];
}

export function consoleHits(messages: readonly string[]): OracleHit[] {
  return messages.map((message) => ({
    oracle: "console",
    severity: "error" as const,
    summary: `页面报错：${message.slice(0, 160)}`,
    detail: message,
    signature: message,
  }));
}

/** 投影自己跟自己对不上：`counts` 与分区数组长度矛盾（与界面无关的纯数据检查）。 */
export function projectionHits(board: BoardData): OracleHit[] {
  const out: OracleHit[] = [];
  for (const slug of Object.keys(board.lengths).sort()) {
    const declared = board.counts[slug];
    const actual = board.lengths[slug];
    if (typeof declared !== "number") continue;
    // 截断的分区只查「自报比实际还少」——多出来是截断，正常。
    const wrong = CAPPED_PARTITIONS.has(slug) ? declared < actual : declared !== actual;
    if (!wrong) continue;
    out.push({
      oracle: "lane_count",
      severity: "error",
      summary: `投影自相矛盾：${slug} 的 counts 说 ${declared}，数组里有 ${actual} 张`,
      detail: `GET /api/board 的 counts.${slug}=${declared}，${slug}[] 的长度是 ${actual}。`,
      signature: `projection ${slug}`,
    });
  }
  return out;
}

/** 看板上那个数字与数据对不上（#446 同类：「待验收 count 与 registry 不一致」）。 */
export function laneCountHits(lanes: readonly LaneSnapshot[], board: BoardData): OracleHit[] {
  const out: OracleHit[] = [];
  for (const lane of lanes) {
    const spec = LANE_BY_SLUG.get(lane.slug);
    if (!spec) continue;
    const shown = parseBadgeTotal(lane.badge);
    const expected = expectedCards(spec, board);
    if (shown === null || expected === null) continue;
    const wrong = laneIsCapped(spec) ? shown < expected : shown !== expected;
    if (!wrong) continue;
    out.push({
      oracle: "lane_count",
      severity: "error",
      summary: `「${lane.slug}」列头写着 ${shown}，数据里是 ${expected} 张`,
      detail: `列头徽章原文 ${JSON.stringify(lane.badge)}；`
        + `GET /api/board 的 ${spec.dataKeys.join(" + ")} 合计 ${expected} 张`
        + `${laneIsCapped(spec) ? "（本列截断，徽章只允许多不允许少）" : ""}。`,
      signature: `lane badge ${lane.slug}`,
    });
  }
  return out;
}

export function alertHits(alerts: readonly AlertSnapshot[]): OracleHit[] {
  return alerts
    .filter((alert) => alert.text.trim())
    .map((alert) => ({
      oracle: "alert",
      severity: "warn" as const,
      summary: `界面在朝用户喊：${alert.text.slice(0, 120)}`,
      detail: `${alert.where}: ${alert.text}`,
      signature: `alert ${alert.where} ${alert.text}`,
    }));
}

export function budgetHits(timings: readonly StepTiming[], budgetMs: number): OracleHit[] {
  return timings
    .filter((timing) => timing.ms > budgetMs)
    .map((timing) => ({
      oracle: "budget",
      severity: "warn" as const,
      summary: `第 ${timing.step} 步 ${timing.action} 花了 ${Math.round(timing.ms)}ms（预算 ${budgetMs}ms）`,
      detail: `动作 ${timing.action} 的往返耗时 ${Math.round(timing.ms)}ms。`,
      signature: `budget ${timing.action.split(" ")[0]}`,
      step: timing.step,
    }));
}

/** 单行省略号容器里文字被裁掉：scrollWidth 比 clientWidth 宽。1px 容差吃掉亚像素。 */
export function overflowHits(boxes: readonly TextBox[]): OracleHit[] {
  return boxes
    .filter((box) => box.clientWidth > 0 && box.scrollWidth > box.clientWidth + 1)
    .map((box) => ({
      oracle: "overflow",
      severity: "warn" as const,
      summary: `文字被裁：${box.where} 里的「${box.text.slice(0, 40)}」`,
      detail: `${box.where} scrollWidth=${box.scrollWidth} > clientWidth=${box.clientWidth}，`
        + `原文 ${JSON.stringify(box.text)}。`,
      signature: `overflow ${box.where}`,
    }));
}

const CJK_RE = /[\u3400-\u4dbf\u4e00-\u9fff]/;

/**
 * 界面语言是 en 时，chrome 里不该有中文。
 *
 * 只查 chrome（页头、左栏、列头、动作按钮、设置区块标题……），不查卡片内容——卡片是用户
 * 数据（demo 场景里本来就是中文），`domainLabel` 对未知枚举也原样回显。采样面由浏览器那边
 * 限定，判官不替它兜底（宪法第 3 条：不报自己没探到的东西）。
 * i18n 没有 key 表（web/src/i18n.ts 是 `text(zh, en)` 内联对），所以没有「key 漏出来」这一类。
 */
export function i18nHits(strings: readonly ChromeString[], lang: string): OracleHit[] {
  if (lang !== "en") return [];
  const out: OracleHit[] = [];
  for (const item of strings) {
    const text = item.text.trim();
    if (!text || !CJK_RE.test(text)) continue;
    out.push({
      oracle: "i18n",
      severity: "warn",
      summary: `英文界面里的中文：「${text.slice(0, 40)}」`,
      detail: `${item.where} 在 lang=en 下显示 ${JSON.stringify(text)}。`,
      signature: `i18n cjk ${item.where}`,
    });
  }
  return out;
}
