// 会议纪要页纯逻辑（§63）：行标签、按日分组、badge 词表、语言选择、复制正文只含 5 行 +
// §63.5 追记的一行表头（issue #299）；§63.8 生成态判定（server 回执 generate_request × 本地乐观 pending）与它的 badge；
// §63.3 追记 校验原因与自动修剪的双语文案（issue #298）；§63.5 追记 三栏判定与已忽略 badge（issue #301）；
// §63.9 行级引用标签 D/S/L/C/O、版本标题、两版逐行差异、回退回执的三态（issue #300）；
// §63.10 两种形状：正文读 daemon 渲染好的 copy_*、可发送长版的「有正文吗」看 sections_en（issue #303）。
// §63.11 意图问答的词表与答案拼装、「转写原版 / 我记录的版本」两版正文与复制（issue #302）。
import { describe, expect, it } from "vitest";
import type { RecapRow } from "../../types";
import {
  LINE_TAGS, LINE_TAG_LABELS, PENDING_TIMEOUT_MS, PICKUP_TIMEOUT_MS, RECAP_ANSWERS_MAX, RECAP_LANES,
  RECAP_SHAPES, RECAP_VIEWS, REVERT_POLL_MS,
  answerLabel, answersFor, appLabel, badgesFor, baselineBody, changedLines, generationPhase, groupByDay,
  hasBaseline, hasRecapText, isGenerating, laneCounts,
  lineCitation, pickLanguage, pickShape, problemLabel, questionLabel, recapBody, recapClipboardText,
  recapHeader, recapLane, recapProblems, recapQuestions, recapRepairs, recapSections, recapShape,
  recapViewBody, repairLabel, revertPhase, rowLabel, slackDraftLabel,
  versionLabel,
} from "./recapText";

function row(over: Partial<RecapRow> = {}): RecapRow {
  return {
    key: "meeting:2026-08-31T1256-zoom",
    app: "zoom",
    start: "2026-08-31T19:56:00Z",
    end: "2026-08-31T20:16:00Z",
    duration_min: 20,
    status: "closed",
    version: 1,
    quality: "ok",
    en: ["Decided: x", "Split: y", "Deadline: z", "Changed since last plan: none recorded", "Open: none"],
    zh: ["定了：x", "分工：y", "截止：z", "较上次变化：无记录", "待定：无"],
    ...over,
  };
}

/** §63.10 一份可发送长版的行：en / zh 是空的，正文在 sections_* 与渲染好的 copy_* 里 */
function sectionsRow(over: Partial<RecapRow> = {}): RecapRow {
  return row({
    shape: "sections",
    en: null,
    zh: null,
    sections_en: [{ key: "decided", modality: "decided", items: ["Ann owns the data mix"] },
                  { key: "proposed", modality: "proposed", items: ["Ship behind a flag", "Re-run the eval"] }],
    sections_zh: [{ key: "decided", modality: "decided", items: ["数据配比归 Ann"] },
                  { key: "proposed", modality: "proposed", items: ["先挂开关上线", "重跑一次评测"] }],
    copy_en: "Decided:\n1. Ann owns the data mix\n\nProposed:\n2. Ship behind a flag\n3. Re-run the eval",
    copy_zh: "定了：\n1. 数据配比归 Ann\n\n提议：\n2. 先挂开关上线\n3. 重跑一次评测",
    ...over,
  });
}

describe("recapText", () => {
  it("labels rows with local times, app name and duration", () => {
    const label = rowLabel(row());
    expect(label).toMatch(/^\d{2}:\d{2}–\d{2}:\d{2} · Zoom · 20 min$/);
    expect(appLabel("slack-huddle")).toBe("Slack Huddle");
    expect(appLabel("unknown-app")).toBe("unknown-app");
    expect(rowLabel(row({ start: "garbage", end: "garbage" }))).toContain("--:--");
  });

  it("groups consecutive rows by local day", () => {
    const a = row({ key: "meeting:2026-08-31T1256-zoom" });
    const b = row({ key: "meeting:2026-08-31T1000-zoom", start: "2026-08-31T17:00:00Z", end: "2026-08-31T17:20:00Z" });
    const c = row({ key: "meeting:2026-08-20T1000-zoom", start: "2026-08-20T17:00:00Z", end: "2026-08-20T17:20:00Z" });
    const groups = groupByDay([a, b, c]);
    expect(groups.length).toBe(2);
    expect(groups[0].rows.map((r) => r.key)).toEqual([a.key, b.key]);
    expect(groups[1].rows).toEqual([c]);
  });

  it("badges follow the #129 vocabulary", () => {
    const ids = (r: RecapRow) => badgesFor(r).map((b) => b.id);
    expect(ids(row())).toEqual(["new"]);
    expect(ids(row({ copied_at: "2026-09-01T00:00:00Z" }))).toEqual(["copied"]);
    expect(ids(row({ copied_at: "x", sent_at: "y" }))).toEqual(["sent"]);
    // §63.5 追记（issue #301）：已忽略盖过已发送 / 已复制 / 新——它是「这场会不需要纪要」的判决
    expect(ids(row({ copied_at: "x", sent_at: "y", dismissed_at: "z" }))).toEqual(["dismissed"]);
    expect(ids(row({ version: 2 }))).toEqual(["new", "updated"]);
    expect(ids(row({ status: "open", en: null, zh: null, quality: null }))).toEqual(["open"]);
    expect(ids(row({ status: "open", partial: true }))).toEqual(["open", "partial"]);
    expect(ids(row({ quality: "needs_review" }))).toEqual(["new", "review"]);
    expect(ids(row({ en: null, zh: null, quality: "thin_transcript" }))).toEqual(["thin"]);
    expect(ids(row({ en: null, zh: null, quality: "no_audio" }))).toEqual(["silent"]);
    expect(ids(row({ en: null, zh: null, quality: "generation_failed" }))).toEqual(["failed"]);
  });

  it("generation phase: a fresh server receipt wins, local pending only bridges the gap (§63.8)", () => {
    const T = 1_000_000;
    const pending = { version: 1, requested_at: null, at: T };
    // 没按过、没回执 = idle；按下后 actd 还没接手 = queued
    expect(generationPhase(row(), undefined, T)).toBe("idle");
    expect(generationPhase(row(), pending, T + 1000)).toBe("queued");
    // 回执落地：running → 以它为准；done 且版本涨了 → idle
    const running = { requested_at: "2026-09-14T12:00:05Z", state: "running", note: null };
    expect(generationPhase(row({ generate_request: running }), pending, T + 5000)).toBe("running");
    expect(generationPhase(row({ generate_request: running }), undefined, T)).toBe("running");   // 刷新页面后仍知道
    const done = { ...running, state: "done" };
    expect(generationPhase(row({ version: 2, generate_request: done }), pending, T + 60_000)).toBe("idle");
    expect(generationPhase(row({ version: 2 }), pending, T + 60_000)).toBe("idle");             // 版本涨了就够
    // 按下时看到的是上一轮的旧回执：它不算新，本地 pending 继续说 queued，直到新 requested_at 出现
    const stale = { requested_at: "2026-09-14T11:00:00Z", state: "lost", note: null };
    const pressedOnStale = { version: 1, requested_at: stale.requested_at, at: T };
    expect(generationPhase(row({ generate_request: stale }), pressedOnStale, T + 1000)).toBe("queued");
    expect(generationPhase(row({ generate_request: stale }), undefined, T)).toBe("lost");
    expect(generationPhase(row({ generate_request: { ...stale, state: "noop", note: "launch_failed" } }), undefined, T)).toBe("noop");
    expect(generationPhase(row({ generate_request: { ...stale, state: "done" } }), undefined, T)).toBe("idle");
    // 90 s 没人接手 → unclaimed（按钮解锁、一句人话）；十分钟退场
    expect(generationPhase(row(), pending, T + PICKUP_TIMEOUT_MS)).toBe("queued");
    expect(generationPhase(row(), pending, T + PICKUP_TIMEOUT_MS + 1)).toBe("unclaimed");
    expect(generationPhase(row(), pending, T + PENDING_TIMEOUT_MS)).toBe("unclaimed");
    expect(generationPhase(row(), pending, T + PENDING_TIMEOUT_MS + 1)).toBe("idle");
    // unclaimed 期间新回执 / 新版本照样接管
    expect(generationPhase(row({ generate_request: running }), pending, T + PICKUP_TIMEOUT_MS + 1)).toBe("running");
    expect(generationPhase(row({ version: 2 }), pending, T + PICKUP_TIMEOUT_MS + 1)).toBe("idle");
    expect(isGenerating("queued") && isGenerating("running")).toBe(true);
    expect(isGenerating("unclaimed") || isGenerating("lost") || isGenerating("noop") || isGenerating("idle")).toBe(false);
  });

  it("generation badges lead the row: 生成中 / 后台未接手 / 生成未落地 / 生成未启动", () => {
    const ids = (r: RecapRow, phase: Parameters<typeof badgesFor>[1]) => badgesFor(r, phase).map((b) => b.id);
    expect(ids(row(), "queued")).toEqual(["generating", "new"]);
    expect(ids(row({ version: 2 }), "running")).toEqual(["generating", "new", "updated"]);
    expect(ids(row(), "unclaimed")).toEqual(["unclaimed", "new"]);
    expect(ids(row(), "lost")).toEqual(["lost", "new"]);
    expect(ids(row(), "noop")).toEqual(["noop", "new"]);
    expect(ids(row(), "idle")).toEqual(["new"]);
    expect(badgesFor(row(), "running")[0]).toMatchObject({ zh: "生成中", en: "Generating", tone: "info" });
    expect(badgesFor(row(), "unclaimed")[0]).toMatchObject({ zh: "后台未接手", en: "Not picked up", tone: "warning" });
    expect(badgesFor(row(), "lost")[0]).toMatchObject({ tone: "warning" });
  });

  it("picks the language: explicit setting wins, auto follows the UI", () => {
    expect(pickLanguage("zh", "en")).toBe("zh");
    expect(pickLanguage("en", "zh")).toBe("en");
    expect(pickLanguage("auto", "zh")).toBe("zh");
    expect(pickLanguage(undefined, "en")).toBe("en");
  });

  it("copy body is exactly the five lines joined by newlines", () => {
    expect(recapBody(row(), "en")).toBe("Decided: x\nSplit: y\nDeadline: z\nChanged since last plan: none recorded\nOpen: none");
    expect(recapBody(row(), "zh").split("\n").length).toBe(5);
    expect(recapBody(row({ en: null }), "en")).toBe("");
  });

  it("the copy header carries the local date and weekday, and the clipboard is header + the same five lines", () => {
    // 本地时间字面量（无 Z）：日期与星期不随 TZ 漂——2026-08-31 是周一。
    const local = row({ start: "2026-08-31T12:56:00", end: "2026-08-31T13:16:00" });
    expect(recapHeader(local, "en")).toBe("2026-08-31 (Mon) 12:56–13:16 · Zoom · 20 min");
    expect(recapHeader(local, "zh")).toBe("2026-08-31（周一） 12:56–13:16 · Zoom · 20 min");
    // 周日 = WEEKDAYS id 1（getDay()+1），表末那一行不能漏
    expect(recapHeader(row({ start: "2026-08-30T09:00:00", end: "2026-08-30T09:20:00" }), "en")).toContain("(Sun)");
    expect(recapHeader(row({ start: "2026-08-30T09:00:00", end: "2026-08-30T09:20:00" }), "zh")).toContain("（周日）");
    // start 坏了：日期位 `?`、不带星期括号、不抛
    const broken = recapHeader(row({ start: "garbage", end: "garbage" }), "en");
    expect(broken).toBe("? --:--–--:-- · Zoom · 20 min");
    expect(broken).not.toContain("(");

    const clipboard = recapClipboardText(local, "en");
    const lines = clipboard.split("\n");
    expect(lines.length).toBe(6);
    expect(lines[0]).toBe(recapHeader(local, "en"));
    expect(lines.slice(1).join("\n")).toBe(recapBody(local, "en"));    // 正文逐字节不动
    expect(recapClipboardText(local, "zh").split("\n").slice(1).join("\n")).toBe(recapBody(local, "zh"));
  });

  it("names the language, the line and the overrun behind needs_review (§63.3 追记)", () => {
    const en = (_zh: string, text: string) => text;
    const zh = (text: string, _en: string) => text;
    expect(problemLabel({ code: "line_too_long", lang: "en", line: 1, limit: 140, over: 6 }, en))
      .toBe("English line 1: 6 characters over the 140-character cap");
    expect(problemLabel({ code: "line_too_long", lang: "zh", line: 3, limit: 60, over: 4 }, zh))
      .toBe("中文第 3 行：超出上限 4 个字符（上限 60）");
    expect(problemLabel({ code: "label_mismatch", lang: "en", line: 2 }, en)).toContain("English line 2: wrong label");
    expect(problemLabel({ code: "reported_speech", lang: "zh", line: 1 }, en)).toContain("Chinese line 1: reported speech");
    // 整语言级的禁项没有行号 → 只说语言，不编一个行号出来
    expect(problemLabel({ code: "timestamp", lang: "en", line: null }, en)).toBe("English: contains a timestamp");
    expect(problemLabel({ code: "link", lang: "zh" }, zh)).toBe("中文：有链接");
    expect(problemLabel({ code: "quotes", lang: "en" }, en)).toBe("English: contains quotation marks");
    expect(problemLabel({ code: "markup", lang: "en" }, en)).toBe("English: contains markdown formatting");
    expect(problemLabel({ code: "emoji", lang: "en" }, en)).toBe("English: contains emoji");
    expect(problemLabel({ code: "mention", lang: "en" }, en)).toBe("English: contains an @mention");
    expect(problemLabel({ code: "line_count", limit: 5 }, en)).toBe("Each language must have exactly five lines");
    // 词表外的新 code（daemon 先行、页面后跟）→ 原样显示它给的那句，绝不显示一行空白
    expect(problemLabel({ code: "future_rule", lang: "en", line: 4, text: "en line 4 broke a new rule" }, en))
      .toBe("en line 4 broke a new rule");
    expect(problemLabel({ code: "future_rule" }, en)).toBe("future_rule");
  });

  it("always says which line was trimmed (§63.3 追记)", () => {
    const en = (_zh: string, text: string) => text;
    const zh = (text: string, _en: string) => text;
    // 说的是真正剪掉的量（`removed`），超出量括注：词边界回退删得比超出量多
    expect(repairLabel({ lang: "en", line: 1, over: 6, removed: 8 }, en))
      .toBe("Trimmed English line 1 automatically: 8 characters off the end (it was 6 over the cap)");
    expect(repairLabel({ lang: "zh", line: 3, over: 2, removed: 2 }, zh))
      .toBe("已自动修剪中文第 3 行：剪掉行尾 2 个字符（原来超出 2 个）");
    // 老 daemon 的行没有 `removed`（add-only）→ 退回只说超出量，绝不显示 undefined
    expect(repairLabel({ lang: "en", line: 1, over: 6 }, en)).toBe("Trimmed English line 1 automatically (it was 6 characters over)");
    // §63.5 追记 2026-09-15 修正：连 `over` 都缺的行（过滤器本会拦下）两个分支也不许说 undefined
    const noOver = { lang: "en", line: 1 } as unknown as Parameters<typeof repairLabel>[0];
    expect(repairLabel(noOver, en)).toBe("Trimmed English line 1 automatically (it was ? characters over)");
    expect(repairLabel({ ...noOver, removed: 8 }, en)).not.toContain("undefined");
  });

  it("wire rows that are not findings are dropped, never rendered", () => {
    expect(recapProblems(row())).toEqual([]);                               // 老 daemon：键缺席
    expect(recapProblems(row({ problems: null }))).toEqual([]);
    expect(recapProblems(row({ problems: "oops" as unknown as [] }))).toEqual([]);
    const good = { code: "line_too_long", lang: "en", line: 1, limit: 140, over: 6 };
    expect(recapProblems(row({ problems: [good, null, { lang: "en" }, 7] as unknown as [] }))).toEqual([good]);
    const trim = { lang: "en", line: 1, over: 6, removed: 8 };
    expect(recapRepairs(row({ repairs: [trim, { lang: "en" }, null] as unknown as [] }))).toEqual([trim]);
    expect(recapRepairs(row())).toEqual([]);
    // §63.5 追记 2026-09-15 修正（R-216 复核）：过滤器收口到法条承诺的形状
    const textOnly = { code: "", text: "en: a rule the page does not know yet" };
    expect(recapProblems(row({ problems: [{ code: "" }, textOnly] as unknown as [] }))).toEqual([textOnly]);   // 空 code 空 text = 一行空白，滤掉
    expect(recapProblems(row({ problems: [{ code: "future_rule", text: { oops: 1 } }] as unknown as [] }))).toEqual([]);  // 对象 text 会让看板崩
    expect(recapProblems(row({ problems: [{ code: "emoji", lang: "en", line: null, limit: null, over: null, text: "en: emoji not allowed" }] }))).toHaveLength(1);  // 整语言级禁项的 null 都合法
    const noRemoved = { lang: "en", line: 1, over: 6 };
    expect(recapRepairs(row({ repairs: [{ line: 1 }, { lang: "en", line: 1 }, noRemoved] as unknown as [] }))).toEqual([noRemoved]);
  });

  it("slack draft receipt copy", () => {
    const text = (_zh: string, en: string) => en;
    expect(slackDraftLabel("posted", text)).toBe("Draft placed");
    expect(slackDraftLabel("draft_already_exists", text)).toBe("Slack already has a draft");
    expect(slackDraftLabel("no_target", text)).toBe("No draft: no target conversation");
    expect(slackDraftLabel("weird", text)).toBe("weird");
    expect(slackDraftLabel(undefined, text)).toBe("");
  });

  // ----- §63.5 追记（issue #301）：一条纪要落在哪一栏 ------------------------------------------ //

  it("files a recap by its two marks: dismissed beats archived, archived is derived from sent", () => {
    expect(recapLane(row())).toBe("active");
    expect(recapLane(row({ status: "open", en: null, zh: null }))).toBe("active");
    expect(recapLane(row({ copied_at: "2026-09-01T00:00:00Z" }))).toBe("active");   // 复制过不算归档
    expect(recapLane(row({ sent_at: "2026-09-01T00:00:00Z" }))).toBe("archived");
    expect(recapLane(row({ sent_at: null }))).toBe("active");                       // 取消标记即恢复
    expect(recapLane(row({ sent_at: "a", dismissed_at: "b" }))).toBe("dismissed");
    expect(RECAP_LANES.map((lane) => lane.id)).toEqual(["active", "archived", "dismissed"]);
  });

  it("counts every lane, including the empty ones", () => {
    const counts = laneCounts([row(), row({ sent_at: "a" }), row({ dismissed_at: "b" }), row({ sent_at: "c" })]);
    expect(counts).toEqual({ active: 1, archived: 2, dismissed: 1 });
    expect(laneCounts([])).toEqual({ active: 0, archived: 0, dismissed: 0 });
  });

  // ----- §63.9（issue #300）：行级引用、版本标题、两版逐行差异 --------------------------------- //

  it("cites a line by its fixed position: date, app and the section tag", () => {
    expect(LINE_TAGS).toEqual(["D", "S", "L", "C", "O"]);
    expect(LINE_TAG_LABELS.map((entry) => entry.tag)).toEqual(LINE_TAGS);
    expect(LINE_TAG_LABELS[3].en).toBe("Changed since last plan");   // 标签文字与 §63.3 的模板逐字同源
    expect(lineCitation(row(), 0)).toMatch(/^\d{4}-\d{2}-\d{2} Zoom #D$/);
    expect(lineCitation(row(), 4)).toMatch(/ Zoom #O$/);
    expect(lineCitation(row({ app: "slack-huddle" }), 1)).toMatch(/ Slack Huddle #S$/);
    expect(lineCitation(row({ start: "garbage" }), 0)).toBe("? Zoom #D");   // 坏 start 恒不抛
    expect(lineCitation(row(), 5)).toBe("");                                // 五行之外没有 chip
  });

  it("the citation never touches the copied body", () => {
    // §63.9 的硬约束：粘出去的 5 行正文逐字节不变（引用是另一次复制）
    const r = row();
    expect(recapBody(r, "en").split("\n")).toEqual(r.en);
    expect(recapClipboardText(r, "en").split("\n").slice(1)).toEqual(r.en);
    expect(recapBody(r, "en")).not.toContain("#D");
  });

  it("compares two versions line by line, with no model and no request", () => {
    const now = ["Decided: a", "Split: not assigned", "Deadline: none"];
    const then = ["Decided: a", "Split: Ann, Bo", "Deadline: none"];
    expect(changedLines(now, then)).toEqual([false, true, false]);
    expect(changedLines(now, now)).toEqual([false, false, false]);
    expect(changedLines([], [])).toEqual([]);
    // 长度不同按位置比（缺的一侧当空串）——手改坏的文件不许让面板崩
    expect(changedLines(["a"], ["a", "b"])).toEqual([false, true]);
    expect(changedLines(["a", "b"], [])).toEqual([true, true]);
  });

  it("titles a stored version with its number, its partial flag and its stamp", () => {
    const text = (_zh: string, en: string) => en;
    expect(versionLabel({ version: 2, generated_at: "2026-08-31T19:56:00Z" }, text))
      .toMatch(/^Version 2 · \d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
    expect(versionLabel({ version: 3, generated_at: null }, text)).toBe("Version 3");
    expect(versionLabel({ version: 3, generated_at: "garbage" }, text)).toBe("Version 3");
    expect(versionLabel({ version: 1, generated_at: null, partial: true }, text)).toBe("Version 1 (partial)");
    const zh = (zhText: string, _en: string) => zhText;
    expect(versionLabel({ version: 1, generated_at: null, partial: true }, zh)).toBe("第 1 版（阶段稿）");
  });

  it("keeps a local receipt for a queued revert, because the daemon keeps none", () => {
    // 回退不进 §63.8 台账（判例 tests/test_recap_revert.py），所以面板只有这条乐观回执可依
    const at = 5_000;
    const pending = { version: 1, base: 2, at };
    const stored = row({ version: 2 });
    expect(revertPhase(stored, null, at)).toBe("idle");                       // 没按过 = 不说话
    expect(revertPhase(stored, pending, at + 1_000)).toBe("queued");
    expect(revertPhase(stored, pending, at + PICKUP_TIMEOUT_MS)).toBe("queued");
    // 90 s 没有新版本 = actd 没在跑 / 起不来 / 锁等超时——与 §63.8 同一条判线
    expect(revertPhase(stored, pending, at + PICKUP_TIMEOUT_MS + 1)).toBe("unclaimed");
    expect(revertPhase(stored, pending, at + PENDING_TIMEOUT_MS + 1)).toBe("idle");   // 10 分钟退场
    // 版本号涨了 = 落地（闪句与脚注接手），回退本身也可能落地成任何一版
    expect(revertPhase(row({ version: 3 }), pending, at + 1_000)).toBe("idle");
    expect(revertPhase(row({ version: 0 }), { version: 1, base: 0, at }, at + 1_000)).toBe("queued");
    // 面板补拉与页面补拉同一个口径，不另起第二套时限
    expect(REVERT_POLL_MS).toBe(5_000);
  });
  // ------------------------------------------------------------------ §63.10
  it("reads the body daemon rendered (copy_*) and falls back to the five lines", () => {
    // 所见即所复制：空的那几行已经在 daemon 侧略掉，client 不再实现第二套略行规则
    expect(recapBody(row({ copy_en: "Decided: x\nSplit: y" }), "en")).toBe("Decided: x\nSplit: y");
    expect(recapBody(sectionsRow(), "en")).toContain("2. Ship behind a flag");
    expect(recapBody(sectionsRow(), "zh")).toContain("提议：");
    // 老 daemon 没有这个键（空串也算没有）= 旧行为一字不变
    expect(recapBody(row({ copy_en: "  " }), "en")).toBe(row().en!.join("\n"));
    expect(recapBody(row(), "en")).toBe(row().en!.join("\n"));
    // 剪贴板仍是「一行表头 + 正文」，表头与 h3 同一份
    expect(recapClipboardText(sectionsRow(), "en").split("\n").slice(1).join("\n"))
      .toBe(recapBody(sectionsRow(), "en"));
  });

  it("knows a sendable recap has text, and which shape a row is", () => {
    expect(recapShape(row())).toBe("lines");
    expect(recapShape(sectionsRow())).toBe("sections");
    expect(recapShape(row({ shape: "garbage" }))).toBe("lines");   // 认不出 = 五行形
    expect(hasRecapText(sectionsRow())).toBe(true);
    expect(hasRecapText(row())).toBe(true);
    expect(hasRecapText(row({ en: null, zh: null }))).toBe(false);
    expect(hasRecapText(row({ en: null, zh: null, sections_en: [] }))).toBe(false);
    // 「空」只有一个判据：节在、渲染出来的正文不在 = 没出稿（否则是一颗复制得到
    // 空字符串的按钮 + 一个空 `<pre>`，而 badge 说「已生成」）
    expect(hasRecapText(sectionsRow({ copy_en: null, copy_zh: null }))).toBe(false);
    // badge 不许因为 en 是空的就把一份可发送长版说成「没出稿」
    expect(badgesFor(sectionsRow()).map((b) => b.id)).toEqual(["new"]);
    expect(badgesFor(sectionsRow({ version: 3, partial: true })).map((b) => b.id))
      .toEqual(["partial", "new", "updated"]);
    expect(RECAP_SHAPES.map((s) => s.id)).toEqual(["lines", "sections"]);
  });

  it("seeds the picker from the row, then from the configured default shape", () => {
    // 出过稿的那一份自己说了算（配置改不动它——粘性在 act/recap.record_shape 那条链上）
    expect(pickShape(sectionsRow(), "lines")).toBe("sections");
    expect(pickShape(row({ shape: "lines" }), "sections")).toBe("lines");
    // 还没出过稿 / 本 PR 之前生成的行没有 shape 键：这时候听配置的
    const legacy = row();
    expect(pickShape(legacy, "sections")).toBe("sections");
    expect(pickShape(legacy, "lines")).toBe("lines");
    // 设置还没拉回来 / 配置被手改坏 = 五行形（与 act 侧的回落同一个结论）
    expect(pickShape(legacy, undefined)).toBe("lines");
    expect(pickShape(legacy, "garbage")).toBe("lines");
  });

  it("only keeps well-formed sections off the wire", () => {
    expect(recapSections(sectionsRow(), "en").map((sec) => sec.key)).toEqual(["decided", "proposed"]);
    const dirty = sectionsRow({ sections_en: [null, 7, { key: "decided" }, { key: "open", modality: "open", items: [] }] as never });
    expect(recapSections(dirty, "en").map((sec) => sec.key)).toEqual(["open"]);
    expect(recapSections(row(), "en")).toEqual([]);
  });

  it("says the sendable shape's validator findings in both languages", () => {
    const zh = (a: string, b: string) => a;
    const en = (a: string, b: string) => b;
    expect(problemLabel({ code: "section_key", lang: "en", line: null }, en)).toMatch(/unknown section/);
    expect(problemLabel({ code: "section_modality", lang: "zh", line: null }, zh)).toContain("语气");
    expect(problemLabel({ code: "item_too_long", lang: "en", line: 3, limit: 240, over: 12 }, en))
      .toBe("English: item 3 is 12 characters over the 240-character cap");
    expect(problemLabel({ code: "item_numbered", lang: "zh", line: 2 }, zh)).toContain("第 2 条");
    expect(problemLabel({ code: "section_mismatch", lang: null, line: null }, en)).toMatch(/must match/);
    expect(problemLabel({ code: "item_count", lang: "en", line: null, limit: 24 }, en)).toMatch(/24 items/);
  });

  // ------------------------------------------------------------------ §63.11
  it("only keeps well-formed intent questions off the wire", () => {
    const asked = row({ questions: [
      { id: "split1", kind: "split", options: ["keep", "drop", "propose"], subject: "Ann: eval" },
      { id: "aud", kind: "audience", options: ["send", "self"], subject: null },
      { id: "broken", kind: "split", options: [] },
      { id: 7, kind: "split", options: ["keep"] },
      null, "nope",
    ] as never });
    expect(recapQuestions(asked).map((q) => q.id)).toEqual(["split1", "aud"]);
    expect(recapQuestions(row())).toEqual([]);            // 老 daemon 无此键 = 不问
  });

  it("sends only the answers the owner actually picked", () => {
    const questions = recapQuestions(row({ questions: [
      { id: "split1", kind: "split", options: ["keep", "drop", "propose"], subject: "Ann: eval" },
      { id: "dl", kind: "deadline", options: ["keep", "drop"], subject: null },
      { id: "aud", kind: "audience", options: ["send", "self"], subject: null },
    ] as never }));
    expect(answersFor(questions, {})).toEqual([]);        // 没点过 = 不发（不编默认值）
    expect(answersFor(questions, { aud: "send", split1: "drop" }))
      .toEqual(["split1=drop", "aud=send"]);              // 顺序跟 wire 上的问题顺序
    // 闭表之外的值不发（手改过的 state / 老词表都可能这样）
    expect(answersFor(questions, { split1: "burn", dl: "drop" })).toEqual(["dl=drop"]);
    const many: Record<string, string> = {};
    const big = recapQuestions(row({ questions: Array.from({ length: 20 }, (_u, i) => (
      { id: `split${i}`, kind: "split", options: ["keep", "drop"], subject: null })) as never }));
    for (const q of big) many[q.id] = "drop";
    expect(answersFor(big, many).length).toBe(RECAP_ANSWERS_MAX);   // = daemon 侧 MAX_ANSWERS
  });

  it("asks each kind in both languages and falls back to the wire value", () => {
    const zh = (a: string, _b: string) => a;
    const en = (_a: string, b: string) => b;
    expect(questionLabel({ id: "split1", kind: "split", options: [] }, en)).toMatch(/obligation/);
    expect(questionLabel({ id: "own", kind: "own", options: [] }, zh)).toContain("认领");
    expect(questionLabel({ id: "x", kind: "brand_new", options: [] }, en)).toBe("brand_new");
    expect(answerLabel("propose", en)).toBe("As a proposal");
    expect(answerLabel("drop", zh)).toBe("删掉");
    expect(answerLabel("brand_new", zh)).toBe("brand_new");
  });

  it("switches between the transcript's version and the recorded one, and copy follows", () => {
    const withBaseline = row({ version: 2, baseline: { version: 1, generated_at: "2026-08-31T20:20:00Z",
      shape: "lines", copy_en: "Decided: Ann owns the data mix", copy_zh: "定了：数据配比归 Ann" } });
    expect(hasBaseline(row())).toBe(false);               // 只生成过一次 = 没有原版可切
    expect(hasBaseline(withBaseline)).toBe(true);
    expect(baselineBody(withBaseline, "zh")).toBe("定了：数据配比归 Ann");
    expect(recapViewBody(withBaseline, "current", "en")).toBe(recapBody(withBaseline, "en"));
    expect(recapViewBody(withBaseline, "baseline", "en")).toBe("Decided: Ann owns the data mix");
    // 复制 = 屏幕上那一份（表头 + 正在看的正文）；默认仍是记录上这一版，老调用方不变
    expect(recapClipboardText(withBaseline, "en", "baseline"))
      .toBe(`${recapHeader(withBaseline, "en")}\nDecided: Ann owns the data mix`);
    expect(recapClipboardText(withBaseline, "en")).toBe(recapClipboardText(withBaseline, "en", "current"));
    // 那一版缺这门语言就退到另一门（空 <pre> 配一颗可用的复制键比一份英文正文差得多）
    const enOnly = row({ baseline: { version: 1, copy_en: "Decided: only English", copy_zh: null } });
    expect(baselineBody(enOnly, "zh")).toBe("Decided: only English");
    expect(hasBaseline(row({ baseline: { version: 1, copy_en: "  ", copy_zh: null } }))).toBe(false);
    expect(hasBaseline(row({ baseline: 7 as never }))).toBe(false);
    expect(RECAP_VIEWS.map((v) => v.id)).toEqual(["baseline", "current"]);
  });
});
