// 会议纪要页右侧详情（CONTRACT §63 / §63.10 / §63.11 / issue #129 §3）：segmented 中文 | English、正文、
// 复制 / 标记已发送 / 重新生成（≤500 字纠正备注）/ OPEN 行「现在生成」/ 开关开着时「投到 Slack 草稿」。
// 唯一出口是剪贴板：复制 = navigator.clipboard + 本地标记（§63.5 追记：写出去的是「一行表头 + 5 行正文」，
// 表头与 h3 同一个 recapHeader(row, language)——所见即所复制；存储仍恰是 5 行）；重新生成 / 投草稿走 inbox 特形动作
// （recap_generate / recap_slack_draft，字段逐字按 §63，多一个键 server 400）。
// §63.8（issue #297）：重新生成排队后面板不再装死——状态行说「排队中 / 正在生成」、两颗生成按钮禁用，
// 新版本随 board 回流落地时闪一句「已更新到第 N 版」（落地无正文则按 quality 说清）；90 s 没人接手说
// 「actd 可能没在跑」并解锁按钮；actd 回执 lost / noop 各一句人话。
// §63.3 追记（issue #298）：needs_review 不再只有一句「校验未通过」——脚注按 wire 的结构化 problems[]
// 逐条说清语言 + 行号 + 超出量（纠正备注可以照着写），落地前被自动修剪的行按 repairs[] 一并摊开。
// §63.9（issue #300）：「上一版…」打开版本面板——`GET /api/recaps/history?key=` 读存着的每一版（正文只在
// 这条路上，看板投影只带标量句柄 history_versions），当前版与选中的旧版并排、逐行标出改动（纯字符串比较，
// 零模型），一颗「回退到这一版」= inbox recap_revert（server 永不写纪要文件，§63.6）；正文下方五颗引用 chip
// 复制 `2026-08-31 Zoom #D`（行位置即身份，粘出去的五行一字不变；逐条 id D1 / A2 等 #303 的多条目格式）。
// 回退没有 daemon 台账（它不是一次生成），所以面板自己记一条乐观回执：排队中一直说话并每 5 s 补拉，
// 90 s 没落地就落回 §63.8 那句「actd 可能没在跑」——闪一句就消失、之后面板装死是这一条要消灭的事。
// 帽的诚实口径：`_push_history` 在历史满 5 版时会挤掉最早那一版，所以回退**也**会老化掉一个回退目标——
// 面板照直说，不许只把老化归因于「下一次生成」。
// §63.5 追记（issue #301）：CLOSED 行多一颗「忽略 / 恢复」（POST /api/recaps/mark dismissed，与
// 「标记已发送」同一个 toggle 机制）——不需要记录的会议不必被迫标成「已发送」才能离开活跃列表；
// 忽略过的行脚注说明它会先被删掉、按「恢复」即撤销。OPEN 行不给这颗按钮（会还没开完，无从判断）。
// §63.10（issue #303）：正文显示的是 daemon 渲染好的 `copy_*`（空的那几行 / 那几节已略掉，
// 可发送长版还带节标题与跨节连续编号）——所见即所复制，渲染只有 act/lib/recap_text 一处。
// 「重新生成」面板多一个形状选择器（快速五行 / 可发送长版），按下时把 `shape` 一并送进
// inbox recap_generate；备注预检因此也按形状收口（可发送长版删得掉一节、写得长一点，
// 再说「做不到」就是错的那句拒绝），命中五行专属那几类时多一句「换成可发送长版就能办到」。
// §63.11（issue #302）：同一个面板里多一组**意图问答**（问题由 daemon 从这一版正文推出来，
// 走 wire 的 `row.questions`；client 不造问题、没点过的问题不发答案），按下时把点过的
// `answers` 一并送出；正文上方多一排「转写原版 | 我记录的版本」——`row.baseline` 在时才出现，
// 复制跟着切换走（`recapClipboardText(row, language, view)`，所见即所复制不因两版并存失效）。
import { useEffect, useRef, useState } from "react";
import { ApiError, fetchRecapHistory, postAction } from "../../api";
import { useI18n, type Language } from "../../i18n";
import { markRecap, markRecapPending, refreshBoard } from "../../store";
import type { RecapHistory, RecapRow, RecapSettings, RecapVersion } from "../../types";
import { copyText } from "../detail/copyText";
import { fixableByLongShape, noteConflicts, type NoteConflictId } from "./noteCheck";
import { RecapIntentPanel } from "./RecapIntent";
import {
  answersFor, changedLines, hasBaseline, hasRecapText, isGenerating, lineCitation, LINE_TAG_LABELS,
  pickLanguage, pickShape,
  problemLabel,
  recapClipboardText, recapHeader, recapProblems, recapQuestions, recapRepairs, recapShape,
  recapViewBody, RECAP_SHAPES, RECAP_VIEWS,
  repairLabel, REVERT_POLL_MS, revertPhase, slackDraftLabel, versionLabel, type GenerationPhase,
  type RecapShape, type RecapView, type RevertPending, type RevertPhase,
} from "./recapText";

const NOTE_MAX = 500;
const CHANNEL_RE = /^[CDG][A-Z0-9]{6,20}$/;

type Bilingual = (zh: string, en: string) => string;

/** §63.5 预检文案：一条诉求一句「为什么做不到」，说的是格式的硬约束，不是模型的脾气。
 *  Record 而非 switch——漏掉一类新 id 是编译错误，不是一条空行。 */
const CONFLICT_LINES: Record<NoteConflictId, (text: Bilingual) => string> = {
  drop_line: (text) =>
    text("删不掉某一行：纪要恒是这五行，每行都带标签；没内容的那行只会写「无」。",
         "A line cannot be dropped: the recap is always these five labelled lines; an empty one comes back as none."),
  add_line: (text) =>
    text("加不了新的一行：只有这五行，多出来的内容只能并进其中一行。",
         "A line cannot be added: there are only these five; anything extra has to fold into one of them."),
  more_detail: (text) =>
    text("写不了更详细：每行有硬性长度上限，超了会被校验判成「需复核」。",
         "More detail does not fit: every line has a hard length cap, and going over it gets the recap flagged needs review."),
  // 注：drop_line / add_line / more_detail 只在「快速五行」下才是做不到的——可发送长版
  // 能删掉一节、多一节、写长一点（§63.10）。面板据 fixableByLongShape 多说一句指路。
  relabel: (text) =>
    text("改不了标签：五个标签的文字与顺序是固定的。",
         "The labels cannot change: their wording and their order are fixed."),
  language_count: (text) =>
    text("改不了语言：中英两版一次产出、都会存下来，上面的切换按钮选看哪版。",
         "The languages cannot change: Chinese and English are always both produced and stored; the tabs above pick which one you read."),
  formatting: (text) =>
    text("加不了格式：加粗、项目符号、emoji、链接、时间戳、引号都会被校验拦下。",
         "Formatting cannot be added: bold, bullets, emoji, links, timestamps and quotation marks are all rejected by the validator."),
};

/** §63.10 选中形状的那一句说明（词表在 recapText.RECAP_SHAPES，文案仍走唯一的 text(zh, en)） */
function shapeHint(shape: RecapShape, text: Text): string {
  const option = RECAP_SHAPES.find((entry) => entry.id === shape);
  return option ? text(option.hint_zh, option.hint_en) : "";
}

export interface RecapDetailProps {
  row: RecapRow;
  settings: RecapSettings | null;
  phase?: GenerationPhase;
}

type Panel = null | "note" | "slack" | "history";
type Text = (zh: string, en: string) => string;

/**
 * §63.9 一版的正文（选中语言）——`RecapVersion.en/zh` 恒是数组（server 侧滤过非字符串项）。
 * §63.10：可发送长版的那一版 `en` / `zh` 是空的，正文在渲染好的 `copy_*` 里——按行切开即可，
 * 两版逐行比对（纯字符串比较）因此对两种形状是同一条路。
 */
function versionLines(entry: RecapVersion | null, language: Language): string[] {
  if (!entry) return [];
  const lines = language === "zh" ? entry.zh : entry.en;
  if (lines && lines.length) return lines;
  const body = language === "zh" ? entry.copy_zh : entry.copy_en;
  return typeof body === "string" && body.trim() ? body.split("\n") : [];
}

/** §63.8 生成态的一句话（idle 不说话；done 由正文与 landedNote 的闪句体现） */
export function generationNote(phase: GenerationPhase, isOpen: boolean, text: Text): string | null {
  switch (phase) {
    case "queued":
      return text("已排队，等待后台接手…", "Queued, waiting for the daemon to pick it up…");
    case "unclaimed":
      return text("后台 90 秒没有接手：actd 可能没在跑（看「依赖检查」区的管线活性）。可以再试一次。", "Nothing picked this up in 90 s: actd may not be running (see Pipeline liveness under Dependency check). You can try again.");
    case "running":
      return isOpen
        ? text("正在生成阶段稿，落地后这里自动更新（通常 1–3 分钟）。", "Generating the partial recap. It lands here by itself (usually 1–3 min).")
        : text("正在重新生成，新版本落地后这里自动更新（通常 1–3 分钟）。", "Regenerating. The new version lands here by itself (usually 1–3 min).");
    case "lost":
      return text("上次生成没有落地：超过 10 分钟没写出新版本——后台进程崩了或模型调用失败（看 state/recap.log）。可以再试一次。", "The last generation never landed: no new version for over 10 minutes. The process crashed or the model call failed (see state/recap.log). You can try again.");
    case "noop":
      return text("上次生成没起来：后台进程启动失败（看 state/actd.log）。可以再试一次。", "The last generation did not start: the background process failed to launch (see state/actd.log). You can try again.");
    default:
      return null;
  }
}

/** §63.9 回退的回执行（idle 不说话；落地由 landedNote 的闪句 + 脚注体现）。
 *  「没人接手」那一句**直接复用 §63.8 的 unclaimed 文案**——同一件事（actd 没在跑 / 起不来 /
 *  锁等超时）只有一句话，不起第二套说法。 */
export function revertNote(phase: RevertPhase, version: number, text: Text): string | null {
  if (phase === "queued") {
    return text(`已排队回退到第 ${version} 版，等待后台接手…（当前这一版会先存进历史，可以再回退回来）`,
                `Revert to version ${version} is queued, waiting for the daemon… (the current text is pushed into history first, so this is undoable)`);
  }
  return phase === "unclaimed" ? generationNote("unclaimed", false, text) : null;
}

/** 新版本落地那一下的闪句：有正文 = 已更新到第 N 版（§63.9 回退落地时点明搬自第几版）；
 *  没正文按 quality 说清为什么（失败不许穿成功的衣） */
export function landedNote(row: RecapRow, text: Text): string {
  const version = row.version ?? 0;
  const from = typeof row.reverted_from === "number" ? row.reverted_from : null;
  if (hasRecapText(row)) {
    return from !== null
      ? text(`已更新到第 ${version} 版（回退自第 ${from} 版）`, `Updated to version ${version} (restored from version ${from})`)
      : text(`已更新到第 ${version} 版`, `Updated to version ${version}`);
  }
  const why = row.quality === "no_audio" ? text("无音频", "no audio")
    : row.quality === "thin_transcript" ? text("转写不全", "thin transcript")
    : text("生成失败", "generation failed");
  return text(`第 ${version} 版没有正文（${why}）`, `Version ${version} landed with no text (${why})`);
}

export interface RecapDiffProps {
  current: RecapVersion | null;
  previous: RecapVersion | null;
  language: Language;
  text: Text;
}

/**
 * §63.9 当前版 × 选中的旧版并排，逐行标出变没变（`changedLines`：纯字符串比较，零模型）。
 * 两列都逐行渲染（不是 `<pre>`）——改动标记必须挂在行上才说得出「哪一行不一样」；
 * 可复制的正文仍然只有上面那个 `<pre>`（所见即所复制的那一份，一字不变）。
 */
export function RecapDiff({ current, previous, language, text }: RecapDiffProps) {
  const now = versionLines(current, language);
  const then = versionLines(previous, language);
  const changed = changedLines(now, then);
  const rows = Array.from({ length: Math.max(now.length, then.length) }, (_unused, i) => i);
  const changedCount = changed.filter(Boolean).length;
  return (
    <div className="recap-history-diff">
      <p className="recap-hint">
        {changedCount === 0
          ? text("这两版的正文逐行相同。", "The two versions are identical line by line.")
          : text(`有 ${changedCount} 行不一样（标了「改」的那几行）。`,
                 `${changedCount} line(s) differ (marked changed below).`)}
      </p>
      <div className="recap-history-cols">
        {([["previous", then], ["current", now]] as const).map(([side, lines]) => (
          <section key={side} className="recap-history-col" aria-label={side === "current"
            ? text("当前版本", "Current version") : text("选中的旧版本", "The stored version")}>
            <h4 className="recap-history-col-title">
              {side === "current"
                ? text(`当前（第 ${current?.version ?? 0} 版）`, `Current (version ${current?.version ?? 0})`)
                : text(`第 ${previous?.version ?? 0} 版`, `Version ${previous?.version ?? 0}`)}
            </h4>
            <ol className="recap-history-lines">
              {rows.map((i) => (
                <li key={i} className={`recap-history-line${changed[i] ? " is-changed" : ""}`}>
                  {changed[i] && (
                    <span className="recap-history-mark" aria-label={text("这一行不一样", "This line differs")}>
                      {text("改", "changed")}
                    </span>
                  )}
                  <span className="recap-history-text">{lines[i] ?? ""}</span>
                </li>
              ))}
            </ol>
          </section>
        ))}
      </div>
    </div>
  );
}

export function RecapDetail({ row, settings, phase = "idle" }: RecapDetailProps) {
  const { text, language: ui } = useI18n();
  const [language, setLanguage] = useState<Language>(pickLanguage(settings?.default_language, ui));
  const [panel, setPanel] = useState<Panel>(null);
  const [note, setNote] = useState("");
  // §63.10 下一次生成用哪种形状（默认 = 这一份现在的形状 > 配置的出厂形状；切行时复位）
  const [pickedShape, setPickedShape] = useState<RecapShape>(pickShape(row, settings?.default_shape));
  // §63.11 意图问答：owner 点过的答案（没点过的问题不在表里 = 不发答案）+ 正在看哪一版
  const [picks, setPicks] = useState<Record<string, string>>({});
  const [view, setView] = useState<RecapView>("current");
  const [channel, setChannel] = useState("");
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  // §63.9：存着的每一版（点开「上一版」才拉；正文不在看板投影里）+ 选中的那一版
  const [history, setHistory] = useState<RecapHistory | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  // §63.9：排队中的回退（daemon 侧没有台账——面板自己的最小乐观回执）+ 逼渲染的秒表
  const [revertPending, setRevertPending] = useState<RevertPending | null>(null);
  const [, setRevertTick] = useState(0);
  // 上一次渲染看到的 {key, version}：同一行版本号涨了 = 新版本落地，闪一句
  const seen = useRef<{ key: string; version: number }>({ key: row.key, version: row.version ?? 0 });

  // 切行 / 语言设置变化 → 语言与面板复位（草稿备注不跨行；上一版的快照也不跨行）
  useEffect(() => {
    setLanguage(pickLanguage(settings?.default_language, ui));
    setPanel(null);
    setNote("");
    setPickedShape(pickShape(row, settings?.default_shape));
    // §63.11：答案与两版切换都不跨行（另一场会的答案套在这一份上就是一次静默改写）
    setPicks({});
    setView("current");
    setFlash(null);
    setHistory(null);
    setHistoryError(null);
    setPicked(null);
    setRevertPending(null);
    // 设置是异步拉来的（挂载时一次）：`default_shape` 落地也要重播一次初值，
    // 否则一行还没出过稿时选择器会停在「快速五行」，而配置说的是可发送长版
  }, [row.key, settings?.default_language, settings?.default_shape, ui]);

  useEffect(() => {
    const version = row.version ?? 0;
    if (seen.current.key === row.key && version > seen.current.version) setFlash(landedNote(row, text));
    seen.current = { key: row.key, version };
  }, [row, text]);

  useEffect(() => {
    if (!flash) return;
    const timer = setTimeout(() => setFlash(null), 4000);
    return () => clearTimeout(timer);
  }, [flash]);

  // §63.9：拉这份纪要存着的每一版（只读端点；失败只让这个面板说话，不动正文）
  async function loadHistory() {
    setHistoryError(null);
    try {
      const snapshot = await fetchRecapHistory(row.key);
      setHistory(snapshot);
      setPicked(snapshot.entries[0]?.version ?? null);
    } catch (error) {
      setHistory(null);
      setHistoryError(error instanceof ApiError ? error.message : String(error));
    }
  }

  // 新版本落地（含刚回退出来的那一版）时把快照重新拉一遍——面板开着就不许显示上一轮的对照
  const landedVersion = row.version ?? 0;
  const historyOpen = panel === "history";
  useEffect(() => {
    if (historyOpen) void loadHistory();
  }, [landedVersion, historyOpen, row.key]);

  // §63.11：正文 = **正在看的那一版**（复制走同一个函数，所见即所复制不因两版切换失效）
  const body = recapViewBody(row, view, language);
  const shape = recapShape(row);
  const questions = recapQuestions(row);
  const baselineReady = hasBaseline(row);
  const viewingBaseline = view === "baseline" && baselineReady;
  // §63.9 有几版可看（老 daemon 没这个键 = 不给入口；有键就逐项都能回退）
  const storedVersions = (row.history_versions ?? []).length;
  const problems = recapProblems(row);
  const repairs = recapRepairs(row);
  const hasText = hasRecapText(row);      // §63.10：可发送长版的正文在 sections_en / copy_en
  const isOpen = row.status === "open";
  const generating = isGenerating(phase);
  const progress = generationNote(phase, isOpen, text);
  // §63.9 回退的回执：每次渲染现算（与 §63.8 页面侧同一口径），落地 / 退场即自己结束
  const reverting = revertPhase(row, revertPending, Date.now());
  const revertProgress = revertNote(reverting, revertPending?.version ?? 0, text);

  // 排队中每 5 s 补拉一次看板（SSE 掉线时的保险，与 §63.8 同款）并逼一次渲染——90 s 那条判线
  // 必须自己会到，不能等下一次 board 回流；unclaimed / 落地后就停（不在途时零请求）
  useEffect(() => {
    if (reverting !== "queued") return;
    const timer = setInterval(() => {
      setRevertTick((n) => n + 1);
      void refreshBoard();
    }, REVERT_POLL_MS);
    return () => clearInterval(timer);
  }, [reverting]);

  // 落地 / 10 分钟退场：把这条本地回执收掉（unclaimed 仍留着——那句话要靠它显示）
  useEffect(() => {
    if (revertPending && reverting === "idle") setRevertPending(null);
  }, [reverting, revertPending]);

  async function run(label: string, action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      setFlash(label);
      setPanel(null);
    } catch (error) {
      setFlash(error instanceof ApiError ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  const copy = () => run(text("已复制到剪贴板", "Copied to clipboard"), async () => {
    const ok = await copyText(recapClipboardText(row, language, view));
    if (!ok) throw new Error(text("复制失败", "Copy failed"));
    await markRecap(row.key, "copied", true);
  });
  const toggleSent = () => run(
    row.sent_at ? text("已取消「已发送」，回到活跃列表", "Sent mark cleared; back in the active list")
                : text("已标记为已发送，归档到「已归档」", "Marked as sent and filed under Archived"),
    () => markRecap(row.key, "sent", !row.sent_at),
  );
  // §63.5 追记：忽略 = 放进「已忽略」并按自己的短保留期先删（恢复撤销它）；不是 registry 回收站（recap 不是卡）
  const toggleDismissed = () => run(
    row.dismissed_at ? text("已恢复到活跃列表", "Restored to the active list")
                     : text("已忽略，放进「已忽略」", "Dismissed and filed under Dismissed"),
    () => markRecap(row.key, "dismissed", !row.dismissed_at),
  );
  // §63.5 预检：备注命中**这一形状**做不到的诉求 → 逐条摊开，按钮改口，toast 不再假装全做到了
  // （§63.10：判定按选中的形状算——选了可发送长版，「删掉那一节」就不再是一句拒绝）
  const conflicts = noteConflicts(note, pickedShape);
  // 五行专属的那几类（删一行 / 加一行 / 写详细）在长版里做得到——多一句指路，而不是只拒绝
  const longShapeHelps = pickedShape === "lines" && fixableByLongShape(conflicts);
  const regenerate = () => run(
    conflicts.length
      ? text("已排队重新生成——上面标出的部分格式做不到，不会变",
             "Regeneration queued — the flagged parts cannot be honored and will not change")
      : text("已排队重新生成，落地后这里自动更新", "Regeneration queued; this panel updates when it lands"),
    async () => {
      const payload: Record<string, unknown> = { action: "recap_generate", meeting_key: row.key,
                                                 shape: pickedShape };
      if (note.trim()) payload.note = note.trim().slice(0, NOTE_MAX);
      // §63.11：只带**点过**的答案（一条也没点 = 键不在，wire 与旧行为一字不差）
      const answers = answersFor(questions, picks);
      if (answers.length) payload.answers = answers;
      await postAction(payload);
      markRecapPending(row);
    });
  const generateNow = () => run(text("已排队生成阶段稿，落地后这里自动更新", "Partial recap queued; this panel updates when it lands"), async () => {
    await postAction({ action: "recap_generate", meeting_key: row.key, partial: true });
    markRecapPending(row);
  });
  const slackDraft = () => run(text("已排队投到 Slack 草稿", "Slack draft queued"), () =>
    postAction({ action: "recap_slack_draft", meeting_key: row.key, channel_id: channel.trim() }));
  // §63.9 回退 = inbox recap_revert（server 永不写纪要文件，§63.6）；非破坏——当前正文先进 history。
  // 闪句只说「已排队」——「落地后自动更新」那句承诺改由上面那条**留在面板上**的回执行兑现：
  // 排队中一直说话，90 s 没落地就说「actd 可能没在跑」，不再闪一下就装死。
  const revert = (version: number) => run(
    text(`已排队回退到第 ${version} 版`, `Revert to version ${version} queued`),
    async () => {
      await postAction({ action: "recap_revert", meeting_key: row.key, version });
      setRevertPending({ version, base: row.version ?? 0, at: Date.now() });
    },
  );
  // §63.9 一行的引用串（`2026-08-31 Zoom #D`）：复制正文一字不变，引用是另一次复制
  const copyCitation = (index: number) => run(text("已复制引用", "Citation copied"), async () => {
    const ok = await copyText(lineCitation(row, index));
    if (!ok) throw new Error(text("复制失败", "Copy failed"));
  });

  return (
    <article className="recap-detail" aria-live="polite">
      <header className="recap-detail-head">
        <h3 className="recap-detail-title">{recapHeader(row, language)}</h3>
        <div className="recap-segmented" role="tablist" aria-label={text("语言", "Language")}>
          {(["zh", "en"] as Language[]).map((lang) => (
            <button
              key={lang}
              type="button"
              role="tab"
              aria-selected={language === lang}
              className={`recap-segment${language === lang ? " is-active" : ""}`}
              onClick={() => setLanguage(lang)}
            >
              {lang === "zh" ? "中文" : "English"}
            </button>
          ))}
        </div>
      </header>

      {progress && (
        <p className={`recap-progress${generating ? " is-busy" : " is-warning"}`} role="status" data-phase={phase}>
          {progress}
        </p>
      )}

      {/* §63.9 回退的回执行：面板收起后仍在（回退没有 daemon 台账，这是唯一的「它到底有没有发生」） */}
      {revertProgress && (
        <p className={`recap-progress${reverting === "queued" ? " is-busy" : " is-warning"}`}
           role="status" data-revert-phase={reverting}>
          {revertProgress}
        </p>
      )}

      {hasText ? (
        <>
          {/* §63.11（issue #302）：第一版是转写说了什么，这一版是我选择记下什么——两版并存可切，
              复制跟着切换走（`recapClipboardText(row, language, view)`）。`baseline` 只在
              第二版落地之后才有，所以只生成过一次的纪要看不到这排按钮。 */}
          {baselineReady && (
            <div className="recap-segmented" role="tablist"
                 aria-label={text("看哪一版", "Which version to read")}>
              {RECAP_VIEWS.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  role="tab"
                  aria-selected={view === option.id}
                  className={`recap-segment${view === option.id ? " is-active" : ""}`}
                  onClick={() => setView(option.id)}
                >
                  {text(option.zh, option.en)}
                </button>
              ))}
            </div>
          )}
          <pre className="recap-body">{body}</pre>
          {viewingBaseline && (
            <p className="recap-hint">
              {text(`这是第 ${row.baseline?.version ?? 1} 版（转写原样出的那一份，不会再变）；复制的就是上面这一份。`,
                    `This is version ${row.baseline?.version ?? 1} — the one straight from the transcript, frozen. Copy takes exactly what you see.`)}
            </p>
          )}
          {/* §63.10：可发送长版的条目每次重新生成都会整批换掉，位置不再是身份——不给一个
              下一版就变的引用，照直说一句它要等跨版稳定的逐条 id（#300 的后半）。 */}
          {shape === "sections" && !viewingBaseline && (
            <p className="recap-hint">
              {text("这一份是可发送长版：条目编号只在这一版里成立，逐条引用要等跨版稳定的条目 id。",
                    "This is the sendable long form: the item numbers hold for this version only — per-item citations need stable item ids first.")}
            </p>
          )}
          {/* §63.9 行级引用：五行的位置就是身份（标签文字与顺序固定），每行一颗 chip 复制
              `2026-08-31 Zoom #D`——粘出去的五行正文一字不变。 */}
          {/* 看着「转写原版」时不给引用 chip：引用串不带版本号，挂在另一版的正文旁边就说不清引的是哪一版 */}
          {shape === "lines" && !viewingBaseline && (
          <div className="recap-cite" role="group" aria-label={text("复制行引用", "Copy a line citation")}>
            <span className="recap-cite-lead">{text("引用：", "Cite:")}</span>
            {LINE_TAG_LABELS.map((entry, index) => (
              <button
                key={entry.tag}
                type="button"
                className="recap-cite-chip"
                disabled={busy}
                title={lineCitation(row, index)}
                aria-label={text(`复制引用 ${lineCitation(row, index)}（${entry.zh}）`,
                                 `Copy citation ${lineCitation(row, index)} (${entry.en})`)}
                onClick={() => void copyCitation(index)}
              >
                {`#${entry.tag}`}
              </button>
            ))}
          </div>
          )}
        </>
      ) : (
        <p className="recap-empty">
          {isOpen
            ? text("会议进行中：结束后 5–35 分钟内自动出稿；也可以现在生成一份阶段稿。", "Meeting in progress: the recap lands 5–35 minutes after it ends; you can also generate a partial one now.")
            : text("这场会没有可用正文（无音频 / 转写不全 / 生成失败）。可以重新生成试试。", "No usable text for this meeting (no audio / thin transcript / generation failed). You can try regenerating.")}
        </p>
      )}

      <div className="recap-actions">
        {hasText && (
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void copy()}>
            {text("复制", "Copy")}
          </button>
        )}
        {hasText && !isOpen && (
          <button type="button" className="btn" disabled={busy} onClick={() => void toggleSent()}>
            {row.sent_at ? text("取消已发送", "Unmark sent") : text("标记已发送", "Mark as sent")}
          </button>
        )}
        {!isOpen && (
          <button type="button" className="btn" disabled={busy} onClick={() => void toggleDismissed()}>
            {row.dismissed_at ? text("恢复", "Restore") : text("忽略", "Dismiss")}
          </button>
        )}
        {storedVersions > 0 && (
          <button type="button" className="btn" disabled={busy}
                  onClick={() => setPanel(panel === "history" ? null : "history")}>
            {text("上一版…", "Previous version…")}
          </button>
        )}
        {isOpen ? (
          <button type="button" className="btn" disabled={busy || generating} onClick={() => void generateNow()}>
            {generating ? text("生成中…", "Generating…") : text("现在生成", "Generate now")}
          </button>
        ) : (
          <button type="button" className="btn" disabled={busy || generating} onClick={() => setPanel(panel === "note" ? null : "note")}>
            {generating ? text("生成中…", "Generating…") : text("重新生成…", "Regenerate…")}
          </button>
        )}
        {settings?.slack_draft_enabled && hasText && !isOpen && (
          <button type="button" className="btn" disabled={busy} onClick={() => setPanel(panel === "slack" ? null : "slack")}>
            {text("投到 Slack 草稿…", "Place in Slack drafts…")}
          </button>
        )}
      </div>

      {panel === "note" && !generating && (
        <div className="recap-panel">
          {/* §63.10 形状选择器：这一次生成出哪一种文档。默认 = 这一份现在的形状（选过一次
              就粘着它，晚到切片的自动重生成也不会把它变回五行）。 */}
          <div className="recap-shape">
            <span className="recap-panel-label" id="recap-shape-label">
              {text("这一份生成成：", "Generate this recap as:")}
            </span>
            <div className="recap-segmented" role="radiogroup" aria-labelledby="recap-shape-label">
              {RECAP_SHAPES.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  role="radio"
                  aria-checked={pickedShape === option.id}
                  className={`recap-segment${pickedShape === option.id ? " is-active" : ""}`}
                  onClick={() => setPickedShape(option.id)}
                >
                  {text(option.zh, option.en)}
                </button>
              ))}
            </div>
            <p className="recap-hint">{shapeHint(pickedShape, text)}</p>
          </div>
          {/* §63.11 意图问答：与形状选择器同一处——这里就是「下一次生成长什么样」的唯一入口
              （D75 的原话），不为几个问题再立第二个面板。 */}
          <RecapIntentPanel questions={questions} picks={picks} text={text} disabled={busy}
                            onPick={(id, option) => setPicks((prev) => {
                              const next = { ...prev };
                              if (next[id] === option) delete next[id];   // 再点一次 = 取消这个答案
                              else next[id] = option;
                              return next;
                            })} />
          <label className="recap-panel-label" htmlFor="recap-note">
            {text("纠正备注（可选，≤500 字）：告诉模型哪里说错了", "Correction note (optional, ≤500 chars): what to fix")}
          </label>
          <textarea
            id="recap-note"
            className="recap-textarea"
            maxLength={NOTE_MAX}
            aria-describedby={conflicts.length ? "recap-note-conflicts" : undefined}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
          {/* 不是 live region：内容随每个按键重算，role="status" 会让读屏在打字中途反复念整张表；
              挂在 textarea 的 aria-describedby 上 = 需要时可达，不追着人念（§63.5）。 */}
          {conflicts.length > 0 && (
            <div className="recap-note-conflicts" id="recap-note-conflicts">
              <p className="recap-note-conflicts-head">
                {pickedShape === "sections"
                  ? text("这几件事可发送长版也做不到，重新生成也不会变：",
                         "The long form cannot honor these either; regenerating will not change them:")
                  : text("这几件事五行格式做不到，重新生成也不会变：",
                         "The five-line format cannot honor these; regenerating will not change them:")}
              </p>
              <ul className="recap-note-conflicts-list">
                {conflicts.map((id) => <li key={id}>{CONFLICT_LINES[id](text)}</li>)}
              </ul>
              {/* §63.10：五行做不到的那几件事，长版做得到——指路，而不是只留一句拒绝 */}
              {longShapeHelps && (
                <p className="recap-hint">
                  {text("其中删掉 / 增加一节、写得更详细，「可发送长版」做得到——在上面切成它再生成。",
                        "Dropping or adding a section and going into more detail do work in the sendable long form — switch to it above and regenerate.")}
                </p>
              )}
            </div>
          )}
          <div className="recap-panel-actions">
            <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void regenerate()}>
              {conflicts.length ? text("仍要重新生成", "Regenerate anyway") : text("重新生成", "Regenerate")}
            </button>
            <span className="recap-hint">{note.length}/{NOTE_MAX}</span>
          </div>
        </div>
      )}

      {panel === "history" && (
        <div className="recap-panel">
          {historyError && <p className="recap-hint">{historyError}</p>}
          {!history && !historyError && <p className="recap-hint">{text("读取中…", "Loading…")}</p>}
          {history && history.truncated && (
            <p className="recap-hint">
              {text("这份纪要的文件太大，没有读取（上一版无法显示）。",
                    "This recap's file is too large to read, so earlier versions cannot be shown.")}
            </p>
          )}
          {history && !history.truncated && history.entries.length === 0 && (
            <p className="recap-hint">
              {text("没有存下来的上一版（这一份只生成过一次，或更早的版本已经老化掉了）。",
                    "No earlier version is stored (this recap was generated once, or the older ones have aged out).")}
            </p>
          )}
          {history && history.entries.length > 0 && (
            <>
              <div className="recap-segmented" role="tablist" aria-label={text("存着的版本", "Stored versions")}>
                {history.entries.map((entry) => (
                  <button
                    key={entry.version}
                    type="button"
                    role="tab"
                    aria-selected={picked === entry.version}
                    className={`recap-segment${picked === entry.version ? " is-active" : ""}`}
                    onClick={() => setPicked(entry.version)}
                  >
                    {versionLabel(entry, text)}
                  </button>
                ))}
              </div>
              <RecapDiff
                current={history.current}
                previous={history.entries.find((entry) => entry.version === picked) ?? null}
                language={language}
                text={text}
              />
              {/* 帽的诚实口径：回退**也**是一次「把当前正文压进历史」，历史满了就同样挤掉最早那一版
                  （act/recap._push_history 的 `[-(HISTORY_CAP - 1):]`）——不许只把老化归因于下一次生成 */}
              <p className="recap-hint">
                {text(`只保留最近 ${history.history_cap} 版：回退会先把当前这一版存进历史（所以能再回退回来），历史已满 ${history.history_cap} 版时最早的那一版会因此老化掉；下一次生成同理。`,
                      `Only the last ${history.history_cap} versions are kept: a revert pushes the current text into history first (so it can be undone), and once history is full at ${history.history_cap} that pushes the oldest stored version out; a regeneration does the same.`)}
              </p>
              {history.entries.length >= history.history_cap && (
                <p className="recap-hint is-warning">
                  {text(`历史已经满 ${history.history_cap} 版了：下一次回退（或生成）会挤掉最早的那一版，它之后就回不去了。`,
                        `History is already full at ${history.history_cap}: the next revert (or regeneration) pushes the oldest stored version out, and it cannot be restored after that.`)}
                </p>
              )}
              <div className="recap-panel-actions">
                <button type="button" className="btn btn-primary"
                        disabled={busy || picked === null || reverting === "queued"}
                        onClick={() => picked !== null && void revert(picked)}>
                  {text("回退到这一版", "Revert to this version")}
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {panel === "slack" && (
        <div className="recap-panel">
          <label className="recap-panel-label" htmlFor="recap-channel">
            {text("Slack 会话 id（C… / D… / G…）——草稿进你的「Drafts & Sent」，发送键仍在你手里", "Slack conversation id (C… / D… / G…) — the draft lands in your Drafts & Sent; sending stays yours")}
          </label>
          <input
            id="recap-channel"
            className="settings-input"
            value={channel}
            placeholder="C0123456789"
            spellCheck={false}
            onChange={(event) => setChannel(event.target.value.trim())}
          />
          <div className="recap-panel-actions">
            <button type="button" className="btn btn-primary" disabled={busy || !CHANNEL_RE.test(channel)} onClick={() => void slackDraft()}>
              {text("投到草稿", "Place draft")}
            </button>
          </div>
        </div>
      )}

      <footer className="recap-meta">
        {row.slack_draft?.status && (
          <span className="recap-meta-item">
            {slackDraftLabel(row.slack_draft.status, text)}
            {row.slack_draft.channel_link && (
              <>
                {" · "}
                <a href={row.slack_draft.channel_link} target="_blank" rel="noreferrer">{text("打开会话", "Open conversation")}</a>
              </>
            )}
          </span>
        )}
        {row.dismissed_at && (
          <span className="recap-meta-item">
            {text("已忽略：会比其他纪要更早被自动删除；按「恢复」撤销。",
                  "Dismissed: it is deleted earlier than the others. Press Restore to undo.")}
          </span>
        )}
        {row.quality === "needs_review" && (
          <span className="recap-meta-item">{text("校验未通过，粘贴前请通读一遍。", "Validator flagged this text; read it before pasting.")}</span>
        )}
        {problems.length > 0 && (
          <ul className="recap-reasons">
            {problems.map((problem, i) => (
              <li key={`${problem.code}-${problem.lang ?? ""}-${problem.line ?? ""}-${i}`}>{problemLabel(problem, text)}</li>
            ))}
          </ul>
        )}
        {repairs.length > 0 && (
          <ul className="recap-reasons is-repair">
            {repairs.map((repair, i) => (
              <li key={`${repair.lang}-${repair.line}-${i}`}>{repairLabel(repair, text)}</li>
            ))}
          </ul>
        )}
        {(row.version ?? 0) > 1 && (
          <span className="recap-meta-item">{text(`第 ${row.version} 版`, `Version ${row.version}`)}</span>
        )}
        {typeof row.reverted_from === "number" && (
          <span className="recap-meta-item">
            {text(`这一版回退自第 ${row.reverted_from} 版（原正文已存进历史，可以再回退回来）。`,
                  `This version was restored from version ${row.reverted_from} (the replaced text is in history, so it can be restored back).`)}
          </span>
        )}
        {row.note && <span className="recap-meta-item">{text("上次备注：", "Last note: ")}{row.note}</span>}
        {/* §63.11：这一版是按几个答案出的（回执属于产出这版正文的那一次生成） */}
        {(row.intent?.answers?.length ?? 0) > 0 && (
          <span className="recap-meta-item">
            {text(`这一版按你回答的 ${row.intent?.answers?.length} 个问题生成。`,
                  `This version was generated from the ${row.intent?.answers?.length} question(s) you answered.`)}
          </span>
        )}
        <span className="recap-meta-item">{text("同室第三人声和系统回声可能混入，粘贴前必读。", "Third-party voices and system echo may leak in — read before pasting.")}</span>
      </footer>

      {flash && <div className="recap-flash" role="status">{flash}</div>}
    </article>
  );
}
