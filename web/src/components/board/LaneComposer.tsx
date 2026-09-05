// 列顶输入框（提案列快速捕获 / 运行中列直跑）。§41 网页纪律（2026-09-04 追记，owner 决策 D35）：
//   - 多行 <textarea>，1 行起随内容增高、5 行封顶后内部滚动（fitComposerRows；行高 / 内边距从
//     computed style 读，单源 = tokens.css 的 --type-composer）；
//   - Enter = 换行，Shift+Enter 也是换行，键盘上没有任何键提交——只有「捕获」/「直跑」按钮提交
//     （owner：「这个我回车我不希望是直接跑而是下一行，要跑是需要点击按钮。」）。不拦 Enter，
//     IME 候选上屏的回车自然安全；⌘↵ 提交没做（owner 要的是只按钮，日后想要再加）；
//   - 草稿保留：仅在 server 确认成功后清空输入框，失败时草稿原样留着；换行原样进 wire text
//     （只到 inbox 文件：actd _capture_text（§10）仍把空白含换行折成单空格——多行是编辑体验，落卡为单行）；
//   - Esc 只交还光标（blur），草稿不动（原生 escKey 的「有草稿只 defocus」半边）；
//   - payload 由调用方经 buildBody(text) 构造（propose = {action:"capture",text}，
//     direct-run = {action:"capture",text,mode:"run"}——多一个字段 server 400）。
//   - 历史 ↑/↓（最近 20 条，localStorage）只在草稿为空或正在翻历史时接管——多行草稿里的 ↑/↓ 归光标；
//     翻历史途中一改字就退出翻历史。斜杠命令 /rec /lang /open（composerCommands.ts，
//     原生 Store.swift / Composer.swift 同款，s4 1.8）——命令不发 inbox，只给一行回执；
//   - 输入框下一行状态（§41 2026-09-05 追记，原生 Composer.swift 的 slashError → hintLine 栈）：失败句优先；
//     否则草稿以 "/" 开头时给命令词表提示行（hintLine）；否则才是成功回执。一改字失败句与回执都清
//     （原生 `.onChange(of: text) { slashError = nil }`；回执是上一次提交的，新草稿一开打就过期）——但 ↑/↓ 翻历史
//     不走 onChange，翻出一条 "/…" 旧捕获时靠渲染处的 `!hint` 守卫保证仍只有一行。原生的键位提示句
//     「↩ 发送 · ⇧↩ 换行 …」随 D35 退役，不补；
//   - 输入框与按钮的 title = 原生 `.help` 提示：直跑「直接开跑：跳过提案与费用预估，成果仍进「待验收」」/
//     捕获「快速捕获（<快捷键>）」——原生写死 ⌘L；web 只在壳（WKWebView）里有键：⌘L（rail 的 window keydown，
//     §54.4 2026-09-05 追记）与全局快速捕获键（§61.6，壳快照 hotkey 如 ⌃⌥Space），写成「⌘L · ⌃⌥Space」；
//     浏览器标签页里 ⌘L 归地址栏、也没有全局键，就不写键，不许谎报。身份（propose / run）从 buildBody 的 payload 读
//     （§34 直跑 = mode:"run"），不另加 prop——wire 形是唯一真源（防腐 #10）。
import { useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { postAction } from "../../api";
import { useI18n } from "../../i18n";
import { useShellState, type ShellState } from "../../shellBridge";
import { describeActionError } from "./boardActions";
import { hintLine, pushHistory, readHistory, runSlashCommand } from "./composerCommands";

interface LaneComposerProps {
  placeholder: string;
  submitLabel: string;
  /** 提交成功后输入框下方的一次性回执文案（如「已提交，AI 分析中…」） */
  successNote: string;
  buildBody: (text: string) => Record<string, unknown>;
}

type ComposerMode = "propose" | "run";

/** 输入框身份从它要发的 payload 读：§34 直跑 = `mode:"run"`，其余 = 提案捕获 */
export function composerMode(buildBody: LaneComposerProps["buildBody"]): ComposerMode {
  return buildBody("").mode === "run" ? "run" : "propose";
}

/** 捕获句里能报的键：只有壳在场才有——原生的 ⌘L（壳里由 rail 的 window keydown 落地，§54.4 2026-09-05 追记）
 *  加壳快照里的全局快速捕获键（⌃⌥Space）；浏览器标签页里 ⌘L 归地址栏，一个键都不报 */
export function quickCaptureKeys(shell: Pick<ShellState, "hotkey"> | null): string | null {
  if (!shell) return null;
  return ["⌘L", shell.hotkey].filter(Boolean).join(" · ");
}

/** 原生 Composer.swift `.help` 两句（Composer.swift:101-104）；捕获句的快捷键 = quickCaptureKeys，没有键就不写键 */
export function composerTitle(mode: ComposerMode, hotkey: string | null, text: (zh: string, en: string) => string): string {
  if (mode === "run") {
    return text("直接开跑：跳过提案与费用预估，成果仍进「待验收」", "Runs now — skips the proposal & cost preview; the result still lands in Review");
  }
  return hotkey ? text(`快速捕获（${hotkey}）`, `Quick capture (${hotkey})`) : text("快速捕获", "Quick capture");
}

/** 失败一行（原生 slashError）：前缀句 + 细节各一节点——「提交失败，已保留输入」/「未识别或参数错误：」+ 原文 */
type ComposerError = { prefix: string; detail?: string };

/** 自动增高的上限行数（原生 Composer.swift `.lineLimit(1...5)`） */
export const COMPOSER_MAX_ROWS = 5;

/** 把 textarea 的 rows 调成内容需要的行数（1…COMPOSER_MAX_ROWS）：先收回 1 行让 scrollHeight 只反映内容，
 *  再按行高换算。行高 / 上下内边距从 computed style 读（token 单源）；jsdom 没有布局（行高空、scrollHeight 0）
 *  → 原样不动，交给判例桩掉这两项。超过上限后 rows 停在上限，textarea 自己的 overflow-y:auto 出滚动条。
 *  空草稿不量、直接 1 行：Chromium / WebKit 都把软换行的 placeholder 算进 scrollHeight（英文 + 大字号时
 *  占位句折两行），量了会让空框长到 2 行、一打字又缩回 1 行（#220 审查）。 */
export function fitComposerRows(el: HTMLTextAreaElement, maxRows = COMPOSER_MAX_ROWS): void {
  if (!el.value) {
    el.rows = 1;
    return;
  }
  const style = window.getComputedStyle(el);
  const lineHeight = parseFloat(style.lineHeight);
  if (!Number.isFinite(lineHeight) || lineHeight <= 0) return;
  const paddingY = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
  el.rows = 1;
  const contentLines = Math.round((el.scrollHeight - paddingY) / lineHeight);
  el.rows = Math.min(maxRows, Math.max(1, contentLines));
}

export function LaneComposer({ placeholder, submitLabel, successNote, buildBody }: LaneComposerProps) {
  const { text } = useI18n();
  const shell = useShellState();
  const title = composerTitle(composerMode(buildBody), quickCaptureKeys(shell), text);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ComposerError | null>(null);
  const [sent, setSent] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [historyIndex, setHistoryIndex] = useState(-1); // -1 = 不在翻历史
  const fieldRef = useRef<HTMLTextAreaElement>(null);

  // 草稿每变一次量一次（输入 / 翻历史 / 成功清空都走这里）
  useLayoutEffect(() => {
    if (fieldRef.current) fitComposerRows(fieldRef.current);
  }, [draft]);

  const submit = async () => {
    const trimmed = draft.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    setSent(false);
    setNote(null);
    try {
      const command = await runSlashCommand(trimmed, text);
      if (command.handled) {
        if ("error" in command) {
          // 原生 Composer.submit 的 slash 失败分支：打错了 → 「未识别或参数错误：」+ 原文（输入保留）；IO 错 → 原句
          setError(command.error.kind === "unrecognized"
            ? { prefix: text("未识别或参数错误：", "Unrecognized or bad argument: "), detail: `${command.error.input} · ${command.error.usage}` }
            : { prefix: command.error.message });
          return;
        }
        setDraft("");
        setNote(command.note);
        return;
      }
      await postAction(buildBody(trimmed));
      pushHistory(trimmed);
      setHistoryIndex(-1);
      setDraft(""); // 仅确认成功后清空（§41 草稿保留）
      setSent(true);
    } catch (e) {
      // capture 写入失败（原生 submitCapture 返回 false）：固定一句 + server 原文；草稿原样留着
      setError({ prefix: text("提交失败，已保留输入", "Submit failed — input kept"), detail: describeActionError(e, text) });
    } finally {
      setBusy(false);
    }
  };

  const recall = (direction: 1 | -1) => {
    const history = readHistory();
    if (history.length === 0) return;
    const next = Math.min(history.length - 1, Math.max(-1, historyIndex + direction));
    setHistoryIndex(next);
    setDraft(next < 0 ? "" : history[next]);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter 不在这里：不拦 = 浏览器原生换行（IME 候选上屏的回车同样不受影响）。提交只有按钮一条路（D35）。
    if (e.nativeEvent.isComposing) return;
    if (e.key === "Escape") {
      e.currentTarget.blur(); // 只交还光标，草稿不动
    } else if (e.key === "ArrowUp" && (draft === "" || historyIndex >= 0)) {
      e.preventDefault();
      recall(1);
    } else if (e.key === "ArrowDown" && historyIndex >= 0) {
      e.preventDefault();
      recall(-1);
    }
  };

  // 原生 Composer.swift 的一行状态栈：slashError > "/" 草稿的 hintLine >（键位提示句，D35 退役）——同一时刻只有一行
  const hint = !error && draft.startsWith("/") ? hintLine(text) : null;

  return (
    <>
      <div className="lane-composer">
        <textarea
          ref={fieldRef}
          rows={1}
          value={draft}
          placeholder={placeholder}
          title={title}
          disabled={busy}
          onChange={(e) => {
            setDraft(e.target.value);
            setError(null); // 一改字失败句即清（原生 `.onChange(of: text) { slashError = nil }`）
            setSent(false); // 上一次提交的回执随之过期（捕获回执与斜杠回执同一规矩）
            setNote(null);
            setHistoryIndex(-1); // 一改字就退出翻历史：↑/↓ 交还给多行草稿里的光标
          }}
          onKeyDown={onKeyDown}
        />
        <button
          type="button"
          className="btn btn-primary"
          title={title}
          disabled={busy || !draft.trim()}
          onClick={() => void submit()}
        >
          {busy ? text("提交中…", "Sending…") : submitLabel}
        </button>
      </div>
      {error && (
        <p className="composer-error">
          <span>{error.prefix}</span>
          {error.detail && <span className="composer-error-detail">{` ${error.detail}`}</span>}
        </p>
      )}
      {hint && <p className="column-help">{hint}</p>}
      {/* `!hint`：↑/↓ 翻出一条 "/…" 旧捕获不走 onChange、回执还在——提示行顶掉它，仍只有一行 */}
      {sent && !error && !hint && <p className="column-help">{successNote}</p>}
      {note && !error && !hint && <p className="column-help">{note}</p>}
    </>
  );
}
