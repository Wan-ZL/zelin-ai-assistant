// ui_scout 的驾驶协议（CONTRACT §79.2）：一步一问一答，问是 Observation，答是 Action。
// 这里是**不可信边界**——Action 是模型吐出来的字节，宪法第 11 条（失败不外溢）+ 防腐
// 「LLM 输出不可信」要求逐字段消毒：认不出的一律降级成一条 invalid 记录，永不抛、永不
// 让一步坏掉的回答把整趟巡检带崩。数字 title / bool deadline 这类真实事故在 radar 侧
// 出过，所以这里对类型一律做**显式**判定，不靠 JS 的隐式转换。
//
// 驾驶员（pilot）是一个子进程：stdin 收一行 Observation JSON，stdout 回一行 Action JSON。
// 模型无关——Claude / Kimi K3 / 任何多模态端点都只要满足这一页。

/** 协议版本；Observation 逐条带上，pilot 认不出就该自己退出（add-only，只增不改）。 */
export const PROTOCOL_VERSION = 1;

/**
 * 跑者等一步回答的默认上限（ms）。驾驶员自己的模型预算必须从这个数派生且严格更短，
 * 这样「没答上来」永远由驾驶员先说出口（一行 give_up），而不是跑者先放弃、回答随后漂回来。
 * 这是两端共用的**唯一真源**：ui_scout_pilot.py 那份镜像由
 * tests/test_ui_scout_pilot_protocol.py 逐字钉着（防腐 #9）。
 */
export const DEFAULT_PILOT_TIMEOUT_MS = 120_000;

/** 驾驶动作词表（一个不多一个不少；未知动词 = invalid）。 */
export const VERBS = [
  "click", "type", "press", "wait", "goto", "report", "done", "give_up",
] as const;
export type Verb = (typeof VERBS)[number];

/** `press` 允许的按键：只留键盘导航与提交，不给组合键（组合键 = 越权面）。 */
export const KEYS = [
  "Enter", "Escape", "Tab", "Backspace", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
] as const;
export type Key = (typeof KEYS)[number];

/** `goto` 允许的落点 = 左栏页面 slug（route.ts 的 `?page=`）；空串 = 看板首页。 */
export const PAGES = ["", "trash", "settings", "skills", "deps", "ingest", "about"] as const;
export type Page = (typeof PAGES)[number];

/** `report` 的严重度（§79.4 的三档；判决口径见 findings.ts）。 */
export const SEVERITIES = ["error", "warn", "info"] as const;
export type Severity = (typeof SEVERITIES)[number];

/** 单条可操作元素——pilot 只能按 `ref` 指认，绝不能自己写 selector（越权面归零）。 */
export interface ElementRef {
  ref: string;
  role: string;
  name: string;
  tag: string;
  enabled: boolean;
  editable: boolean;
}

/** 一步的观察面：截图路径 + 可操作元素 + 可见文本 + 上一步的回执。 */
export interface Observation {
  protocol: number;
  journey: string;
  goal: string;
  /** 沙箱须知：这一趟里哪些「看起来不对」其实是夹具使然——防假红的第一道闸（§79.1）。 */
  notes: string;
  step: number;
  maxSteps: number;
  url: string;
  page: string;
  lang: string;
  viewport: { width: number; height: number };
  screenshot: string;
  elements: ElementRef[];
  text: string;
  lastAction: string | null;
  lastError: string | null;
  /**
   * 这一屏还没落定（转圈的 spinner / 卡面「处理中」小字还在）。
   * 跑者每一步已经先等过一轮上限，仍是 true 说明它真的在等——驾驶员可以选择再 `wait`
   * 一下再动手，而不是对着一个半渲染的界面点下去。
   */
  settling: boolean;
}

export type Action =
  | { verb: "click"; ref: string }
  | { verb: "type"; ref: string; text: string }
  | { verb: "press"; key: Key }
  | { verb: "wait"; ms: number }
  | { verb: "goto"; page: Page }
  | { verb: "report"; severity: Severity; summary: string; detail: string }
  | { verb: "done"; summary: string }
  | { verb: "give_up"; summary: string };

export type ActionParse =
  | { ok: true; action: Action }
  | { ok: false; reason: string };

/** `type` / `report` 的文本上限：够写一条复现步骤，挡得住把整页粘回来。 */
export const MAX_TEXT = 2000;
/** `wait` 的上限：单步预算是秒级，5 s 已经是「这一步明显卡住」的证据。 */
export const MAX_WAIT_MS = 5000;

const VERB_SET: ReadonlySet<string> = new Set(VERBS);
const KEY_SET: ReadonlySet<string> = new Set(KEYS);
const PAGE_SET: ReadonlySet<string> = new Set(PAGES);
const SEVERITY_SET: ReadonlySet<string> = new Set(SEVERITIES);

/** 控制字符清洗：换行与制表留着（多行输入是真需求），其余一律去掉。 */
export function scrubText(value: unknown): string {
  if (typeof value !== "string") return "";
  let out = "";
  for (const ch of value) {
    const code = ch.codePointAt(0) ?? 0;
    if (ch === "\n" || ch === "\t") out += ch;
    else if (code >= 0x20 && code !== 0x7f) out += ch;
  }
  return out.slice(0, MAX_TEXT);
}

/** 数字消毒：真数字或十进制数字串才算；NaN / Infinity / 其它一律 null。 */
export function scrubNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && /^-?\d+(\.\d+)?$/.test(value.trim())) {
    const parsed = Number(value.trim());
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function refOf(raw: Record<string, unknown>, refs: ReadonlySet<string>): string | null {
  const ref = raw.ref;
  if (typeof ref !== "string" || !refs.has(ref)) return null;
  return ref;
}

function parseClick(raw: Record<string, unknown>, refs: ReadonlySet<string>): ActionParse {
  const ref = refOf(raw, refs);
  if (ref === null) return { ok: false, reason: "click 的 ref 不在本步发出的元素表里" };
  return { ok: true, action: { verb: "click", ref } };
}

function parseType(raw: Record<string, unknown>, refs: ReadonlySet<string>): ActionParse {
  const ref = refOf(raw, refs);
  if (ref === null) return { ok: false, reason: "type 的 ref 不在本步发出的元素表里" };
  return { ok: true, action: { verb: "type", ref, text: scrubText(raw.text) } };
}

function parsePress(raw: Record<string, unknown>): ActionParse {
  const key = raw.key;
  if (typeof key !== "string" || !KEY_SET.has(key)) return { ok: false, reason: "press 的 key 不在词表里" };
  return { ok: true, action: { verb: "press", key: key as Key } };
}

function parseWait(raw: Record<string, unknown>): ActionParse {
  const ms = scrubNumber(raw.ms);
  if (ms === null) return { ok: false, reason: "wait 的 ms 不是数字" };
  return { ok: true, action: { verb: "wait", ms: Math.min(Math.max(ms, 0), MAX_WAIT_MS) } };
}

function parseGoto(raw: Record<string, unknown>): ActionParse {
  const page = raw.page;
  if (typeof page !== "string" || !PAGE_SET.has(page)) return { ok: false, reason: "goto 的 page 不在词表里" };
  return { ok: true, action: { verb: "goto", page: page as Page } };
}

function parseReport(raw: Record<string, unknown>): ActionParse {
  const summary = scrubText(raw.summary);
  if (!summary) return { ok: false, reason: "report 没有 summary" };
  const raw_severity = raw.severity;
  const severity = (typeof raw_severity === "string" && SEVERITY_SET.has(raw_severity)
    ? raw_severity : "warn") as Severity;
  return { ok: true, action: { verb: "report", severity, summary, detail: scrubText(raw.detail) } };
}

function parseFinish(verb: "done" | "give_up", raw: Record<string, unknown>): ActionParse {
  return { ok: true, action: { verb, summary: scrubText(raw.summary) } };
}

const PARSERS: Record<Verb, (raw: Record<string, unknown>, refs: ReadonlySet<string>) => ActionParse> = {
  click: parseClick,
  type: parseType,
  press: (raw) => parsePress(raw),
  wait: (raw) => parseWait(raw),
  goto: (raw) => parseGoto(raw),
  report: (raw) => parseReport(raw),
  done: (raw) => parseFinish("done", raw),
  give_up: (raw) => parseFinish("give_up", raw),
};

/**
 * 一条 pilot 回答 → 可执行动作，或一条拒绝理由。
 *
 * `refs` = 本步 Observation 里发出去的 ref 全集：pilot 只能指认我们给过的元素，
 * 别的一律拒（这是越权面唯一的闸）。认不出来永远是 `{ok:false}`，绝不抛。
 */
export function parseAction(raw: unknown, refs: Iterable<string>): ActionParse {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    return { ok: false, reason: "回答不是 JSON 对象" };
  }
  const record = raw as Record<string, unknown>;
  const verb = record.verb;
  if (typeof verb !== "string" || !VERB_SET.has(verb)) {
    return { ok: false, reason: `未知动词 ${JSON.stringify(record.verb)}` };
  }
  return PARSERS[verb as Verb](record, new Set(refs));
}

/** 回答里回声的 step；不是对象、没这个字段、不是数字，一律 null（= 没回声）。 */
function echoedStep(parsed: unknown): number | null {
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  return scrubNumber((parsed as Record<string, unknown>).step);
}

/**
 * pilot 的一行 stdout → 动作。JSON 坏了也只是一条拒绝理由（宪法第 11 条）。
 *
 * `step` = 本步 Observation 的步号，**必须**被回答原样回声回来——这是「一步一问一答」
 * 唯一的配对证据。ref 闸挡不住这种错配：ref 每步都按文档序重新编号，上一步漂回来的
 * `e7` 在这一步几乎总能对上号，只是指向了另一个元素——于是整趟答案错位一步，巡检自己点
 * 出来的状态变化会被当成产品 bug 报上去。回声对不上就丢，宁可这一步失败。
 *
 * 回声由**传输层**盖章（参考实现在 ui_scout_pilot.py 里按读到的 Observation 盖），
 * 不靠模型记得写——协议的事不交给模型的记性。
 */
export function parseActionLine(line: string, refs: Iterable<string>, step: number): ActionParse {
  const trimmed = line.trim();
  if (!trimmed) return { ok: false, reason: "pilot 回了空行" };
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return { ok: false, reason: "pilot 的回答不是合法 JSON" };
  }
  const echoed = echoedStep(parsed);
  if (echoed !== step) {
    return { ok: false, reason: `回答回声的 step 是 ${JSON.stringify(echoed)}，本步问的是 ${step}（答非所问，丢弃）` };
  }
  return parseAction(parsed, refs);
}

/** 动作 → 一行人类可读的复现步骤（报告与 lastAction 回执共用）。 */
export function describeAction(action: Action): string {
  switch (action.verb) {
    case "click": return `click ${action.ref}`;
    case "type": return `type ${action.ref} ${JSON.stringify(action.text)}`;
    case "press": return `press ${action.key}`;
    case "wait": return `wait ${action.ms}ms`;
    case "goto": return `goto ?page=${action.page}`;
    case "report": return `report[${action.severity}] ${action.summary}`;
    default: return `${action.verb} ${action.summary}`;
  }
}
