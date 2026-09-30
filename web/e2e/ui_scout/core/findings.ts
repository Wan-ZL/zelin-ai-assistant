// ui_scout 的发现与指纹（CONTRACT §79.4）。
//
// 一条发现 = 谁（journey）+ 哪只 oracle + 一句话 + 复现步骤。指纹是**唯一**一处
// 计算（防腐 #9 命名单源）：TS 这边算出来写进报告，scripts/qa/ui_scout.py 只读不重算，
// 免得两处规则漂移成两套去重口径。
//
// 判决口径（宪法第 3 条：只报真实探测结果）：
//   error = 确定性坏了（pageerror / console error / 动作真的炸了 / 计数对不上）；
//   warn  = 值得看但可能是环境（单步超预算、文字裁切、疑似没翻译）；
//   info  = pilot 自己说的观察，未经确定性 oracle 证实。
// 巡检的退出码只看 error（warn 上 CI 会变成噪音；§79.6 的边界）。

import type { Severity } from "./protocol";

/**
 * 判红的门槛：只有 error 让巡检判红（§79.6）。
 *
 * 这是**唯一**一处：跑者的「巡检判决」拿它过滤，`scripts/qa/ui_scout.py` 的 `--fail-on`
 * 默认值是它的镜像，由 tests/test_ui_scout_pilot_protocol.py 逐字钉着。一个没人读的
 * 「单一真源」比没有更坏——它看起来像个闸，实际上谁都没关（防腐 #9）。
 */
export const FAIL_SEVERITY: Severity = "error";

export interface Finding {
  journey: string;
  oracle: string;
  severity: Severity;
  summary: string;
  detail: string;
  step: number;
  url: string;
  screenshot: string | null;
  fingerprint: string;
}

/** oracle 产出的半成品：指纹与上下文由巡检循环补齐。 */
export interface OracleHit {
  oracle: string;
  severity: Severity;
  summary: string;
  detail: string;
  /** 指纹只吃这一段——把可变的数字/路径/时间留在 summary 里，别塞进来。 */
  signature: string;
  /**
   * 这一条说的是第几步。逐帧判官不用填（用当前步），但**跨步**的判官必须填——
   * 预算判官是在一趟走完之后回头看时间表的，不填就会把每一条都记到同一步上。
   */
  step?: number;
}

const SEVERITY_ORDER: Record<Severity, number> = { error: 3, warn: 2, info: 1 };

/**
 * 签名归一：把「同一个 bug 每次跑都换一个数字」的部分抹平，指纹才稳得住。
 * 抹平项：十六进制 id、纯数字、ISO 时间戳、绝对路径、URL 的 query。
 */
export function normalizeSignature(text: string): string {
  return text
    .replace(/\d{4}-\d{2}-\d{2}T[\d:.]+Z?/g, "<ts>")
    .replace(/\/[\w./-]{8,}/g, "<path>")
    .replace(/\?[\w=&%-]+/g, "<query>")
    .replace(/\b[0-9a-f]{8,}\b/gi, "<hex>")
    .replace(/\d+/g, "<n>")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

// 指纹三段之间的分隔符：NUL 不可能出现在 journey / oracle / 签名里，所以
// 「a|bc」与「ab|c」永远算出不同的指纹。写成 fromCharCode 是为了源码里不出现控制字节。
const SEP = String.fromCharCode(0);

/** FNV-1a 32 位 → 8 位十六进制。要的是稳定与短，不是抗碰撞。 */
export function shortHash(text: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

/** 指纹 = journey + oracle + 归一后的签名。同一个 bug 跨跑次同一个指纹。 */
export function fingerprint(journey: string, oracle: string, signature: string): string {
  return shortHash(journey + SEP + oracle + SEP + normalizeSignature(signature));
}

export interface FindingContext {
  journey: string;
  step: number;
  url: string;
  screenshot: string | null;
}

export function toFinding(hit: OracleHit, ctx: FindingContext): Finding {
  return {
    journey: ctx.journey,
    oracle: hit.oracle,
    severity: hit.severity,
    summary: hit.summary,
    detail: hit.detail,
    step: hit.step ?? ctx.step,
    url: ctx.url,
    screenshot: ctx.screenshot,
    fingerprint: fingerprint(ctx.journey, hit.oracle, hit.signature),
  };
}

/** 同指纹只留第一条（第一条带着最早的复现步骤，最有用）。 */
export function dedupe(findings: readonly Finding[]): Finding[] {
  const seen = new Set<string>();
  const out: Finding[] = [];
  for (const finding of findings) {
    if (seen.has(finding.fingerprint)) continue;
    seen.add(finding.fingerprint);
    out.push(finding);
  }
  return out;
}

export function worstSeverity(findings: readonly Finding[]): Severity | null {
  let worst: Severity | null = null;
  for (const finding of findings) {
    if (worst === null || SEVERITY_ORDER[finding.severity] > SEVERITY_ORDER[worst]) {
      worst = finding.severity;
    }
  }
  return worst;
}

export function countBySeverity(findings: readonly Finding[]): Record<Severity, number> {
  const out: Record<Severity, number> = { error: 0, warn: 0, info: 0 };
  for (const finding of findings) out[finding.severity] += 1;
  return out;
}
