// ui_scout 的报告（CONTRACT §79.5）：一次巡检 = 一份 report.json + 一份自包含的 report.html。
//
// report.json 是机器面（scripts/qa/ui_scout.py 读它出判决），report.html 是人面：一趟一节、
// 一步一张截图、每条发现配上「第几步、在哪个 URL、点了什么」——照着就能复现。
// 截图路径一律是**相对**路径（相对 report.html 自己），所以整个 run 目录可以原样拷走。

import type { Finding } from "./findings";
import { countBySeverity } from "./findings";

export interface StepRecord {
  step: number;
  action: string;
  ok: boolean;
  error: string | null;
  /** 界面的往返耗时——预算判官只看这一笔（§79.3） */
  ms: number;
  /** 驾驶员想了多久（离线的接近 0，模型驾驶时是大头）；只做记录，不判红 */
  pilotMs: number;
  url: string;
  page: string;
  screenshot: string | null;
}

export interface JourneyRecord {
  name: string;
  scene: string;
  lang: string;
  goal: string;
  viewport: { width: number; height: number };
  outcome: "done" | "gave_up" | "out_of_steps";
  reached: boolean;
  steps: StepRecord[];
}

export interface ScoutCounts {
  journeys: number;
  steps: number;
  error: number;
  warn: number;
  info: number;
}

export interface ScoutReport {
  protocol: number;
  pilot: string;
  startedAt: string;
  durationMs: number;
  journeys: JourneyRecord[];
  findings: Finding[];
  counts: ScoutCounts;
}

export function summarize(journeys: readonly JourneyRecord[], findings: readonly Finding[]): ScoutCounts {
  const bySeverity = countBySeverity(findings);
  return {
    journeys: journeys.length,
    steps: journeys.reduce((total, journey) => total + journey.steps.length, 0),
    error: bySeverity.error,
    warn: bySeverity.warn,
    info: bySeverity.info,
  };
}

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const STYLE = `
:root { color-scheme: light dark; }
body { font: 14px/1.6 -apple-system, "PingFang SC", sans-serif; margin: 0 auto; max-width: 68rem; padding: 2rem 1.5rem; }
h1 { font-size: 1.5rem; margin-bottom: .25rem; }
h2 { font-size: 1.15rem; margin-top: 2.5rem; border-bottom: 1px solid #8884; padding-bottom: .3rem; }
h3 { font-size: 1rem; margin-top: 1.5rem; }
table { border-collapse: collapse; width: 100%; margin: .75rem 0; }
th, td { border: 1px solid #8884; padding: .35rem .5rem; text-align: left; vertical-align: top; }
th { background: #8881; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .9em; }
.sev-error { color: #b3261e; font-weight: 600; }
.sev-warn { color: #9a6700; font-weight: 600; }
.sev-info { color: #4a5568; }
.shots { display: flex; flex-wrap: wrap; gap: .5rem; margin: .5rem 0; }
.shots figure { margin: 0; width: 15rem; }
.shots img { width: 100%; border: 1px solid #8884; border-radius: 4px; }
.shots figcaption { font-size: .75rem; color: #666; }
.muted { color: #666; }
`;

function findingRows(findings: readonly Finding[]): string {
  if (!findings.length) return "<p>这一趟没有发现。</p>";
  const rows = findings.map((finding) => `<tr>
<td class="sev-${finding.severity}">${escapeHtml(finding.severity)}</td>
<td><code>${escapeHtml(finding.oracle)}</code></td>
<td>${escapeHtml(finding.summary)}<br><span class="muted">${escapeHtml(finding.detail)}</span></td>
<td>第 ${finding.step} 步<br><code>${escapeHtml(finding.fingerprint)}</code></td>
</tr>`).join("\n");
  return `<table><thead><tr><th>严重度</th><th>判官</th><th>说的是什么</th><th>在哪</th></tr></thead>
<tbody>${rows}</tbody></table>`;
}

function stepRows(steps: readonly StepRecord[]): string {
  const rows = steps.map((step) => `<tr>
<td>${step.step}</td>
<td><code>${escapeHtml(step.action)}</code></td>
<td>${step.ok ? "ok" : `<span class="sev-error">${escapeHtml(step.error ?? "失败")}</span>`}</td>
<td>${Math.round(step.ms)}ms</td>
<td class="muted">${Math.round(step.pilotMs)}ms</td>
<td><code>${escapeHtml(step.page)}</code></td>
</tr>`).join("\n");
  return `<table><thead><tr><th>#</th><th>动作</th><th>结果</th><th>界面耗时</th><th>驾驶员想了多久</th><th>页面</th></tr></thead>
<tbody>${rows}</tbody></table>`;
}

function shots(steps: readonly StepRecord[]): string {
  const withShots = steps.filter((step) => step.screenshot);
  if (!withShots.length) return "";
  // 截图是**动作之前**那一帧——驾驶员正是看着它决定要做什么的。
  const figures = withShots.map((step) => `<figure>
<a href="${escapeHtml(step.screenshot as string)}"><img src="${escapeHtml(step.screenshot as string)}" alt="第 ${step.step} 步动作前"></a>
<figcaption>第 ${step.step} 步之前 → 然后 ${escapeHtml(step.action)}</figcaption>
</figure>`).join("\n");
  return `<div class="shots">${figures}</div>`;
}

function journeySection(journey: JourneyRecord, findings: readonly Finding[]): string {
  const mine = findings.filter((finding) => finding.journey === journey.name);
  return `<h2>${escapeHtml(journey.name)}</h2>
<p class="muted">场景 <code>${escapeHtml(journey.scene)}</code> · 语言 <code>${escapeHtml(journey.lang)}</code>
 · 视口 ${journey.viewport.width}×${journey.viewport.height}
 · 结局 <code>${escapeHtml(journey.outcome)}</code>
 · 到达判据 ${journey.reached ? "成立" : "<span class=\"sev-warn\">不成立</span>"}</p>
<p><strong>目标</strong>：${escapeHtml(journey.goal)}</p>
<h3>发现</h3>
${findingRows(mine)}
<h3>走了哪些步</h3>
${stepRows(journey.steps)}
${shots(journey.steps)}`;
}

export function renderReportHtml(report: ScoutReport): string {
  const sections = report.journeys.map((journey) => journeySection(journey, report.findings)).join("\n");
  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ui_scout 巡检报告 · ${escapeHtml(report.startedAt)}</title>
<style>${STYLE}</style>
</head>
<body>
<h1>ui_scout 巡检报告</h1>
<p class="muted">驾驶员 <code>${escapeHtml(report.pilot)}</code>
 · 协议 v${report.protocol}
 · 开始于 ${escapeHtml(report.startedAt)}
 · 用时 ${Math.round(report.durationMs / 1000)}s</p>
<table>
<thead><tr><th>行程</th><th>步数</th><th class="sev-error">error</th><th class="sev-warn">warn</th><th class="sev-info">info</th></tr></thead>
<tbody><tr><td>${report.counts.journeys}</td><td>${report.counts.steps}</td>
<td>${report.counts.error}</td><td>${report.counts.warn}</td><td>${report.counts.info}</td></tr></tbody>
</table>
<h2>全部发现</h2>
${findingRows(report.findings)}
${sections}
</body>
</html>
`;
}
