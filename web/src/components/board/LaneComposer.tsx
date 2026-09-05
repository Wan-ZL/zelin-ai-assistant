// 列顶输入框（提案列快速捕获 / 运行中列直跑）。§41 网页纪律（2026-09-04 追记，owner 决策 D35）：
//   - 多行 <textarea>，1 行起随内容增高、5 行封顶后内部滚动（fitComposerRows；行高 / 内边距从
//     computed style 读，单源 = tokens.css 的 --type-composer）；
//   - Enter = 换行，Shift+Enter 也是换行，键盘上没有任何键提交——只有「捕获」/「直跑」按钮提交
//     （owner：「这个我回车我不希望是直接跑而是下一行，要跑是需要点击按钮。」）。不拦 Enter，
//     IME 候选上屏的回车自然安全；⌘↵ 提交没做（owner 要的是只按钮，日后想要再加）；
//   - 草稿保留：仅在 server 确认成功后清空输入框，失败时草稿原样留着；换行原样进 wire text
//     （只到 inbox 文件：actd _capture_text（§10）仍把空白含换行折成单空格——多行是编辑体验，落卡为单行）；
//   - Esc 只交还光标（blur），草稿不动（原生 escKey 的「有草稿只 defocus」半边）；且**在框内就地吃掉**
//     （stopPropagation——原生 escKey 返 `.handled`，Esc 永不外泄到看板层：FilterBar 的 window ⎋ 不许因此
//     清掉 ⌘F 搜索词 / 退出多选，§34 2026-09-05 追记）；IME 候选期间的 Esc 归输入法（不 blur，原生 hasMarkedText
//     返 `.ignored`），但同样不外泄；
//   - payload 由调用方经 buildBody(text) 构造（propose = {action:"capture",text}，
//     direct-run = {action:"capture",text,mode:"run"}——多一个字段 server 400）。
//   - 历史 ↑/↓（最近 20 条，localStorage）只在草稿为空或正在翻历史时接管——多行草稿里的 ↑/↓ 归光标；
//     翻历史途中一改字就退出翻历史。斜杠命令 /rec /lang /open（composerCommands.ts，
//     原生 Store.swift / Composer.swift 同款，s4 1.8）——命令不发 inbox，只给一行回执；**成功的命令也进历史**
//     （原生 AppDelegate.submitCapture `if ok { CaptureHistory.push(text) }  // item 5: commands count too`），
//     所以 `/lang en` 之后 ↑ 能翻回它；命令报错不进历史（原生 ok=false 不 push）；
//   - 输入框下一行状态（§41 2026-09-05 追记，原生 Composer.swift 的 slashError → hintLine 栈）：失败句优先；
//     否则草稿以 "/" 开头时给命令词表提示行（hintLine）；否则才是回执。一改字失败句与**斜杠回执**清
//     （原生 `.onChange(of: text) { slashError = nil }`；「语言 → en」说的是上一次命令，新草稿一开打就过期）——
//     ↑/↓ 翻历史不走 onChange，翻出一条 "/…" 旧捕获时靠渲染处的 `!hint` 守卫保证仍只有一行。原生的键位提示句
//     「↩ 发送 · ⇧↩ 换行 …」随 D35 退役，不补；
//   - **捕获回执活过键击**（§10 / §41 2026-09-05 追记，captureReceipt.ts）：「「<原话前 20 字>」已提交，AI 分析中…」
//     是原生本地占位卡的 web 替身（那张卡不搬：server 的 processing / queued 行一个 actd pass 内就落列，防腐 #10），
//     它的寿命也照占位卡的规矩——刷新带来一行属于这次提交的卡即清（先认 §10 issue #7 的 capture_id = POST 回的 stem，
//     再退到原生 captureMatches 的标题 / 摘要前缀猜测；提交那一刻的快照不算），否则 300 s / 180 s 后换成
//     原生的诚实超时条（管线 ok 时才计时，恢复时重新起算）；状态句随管线健康切换（P1-4：不 ok 时说「已保存到
//     队列」而不是许诺「2-3 分钟」）。只有下一次**成功的捕获**才替换它（原生 writeInboxFile 失败不 beginCapture、
//     斜杠命令不进 store）：失败句 / 提示行 / 斜杠回执只是按一行栈暂时顶掉它，一改字它们过期后回执回来，
//     时钟全程没停（useCaptureReceipt.ts，与「清理积压」按钮共用）；
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
import { captureReceiptLine, captureTimeoutNotice, type CaptureMode } from "./captureReceipt";
import { hintLine, pushHistory, readHistory, runSlashCommand } from "./composerCommands";
import { useCaptureReceipt } from "./useCaptureReceipt";

interface LaneComposerProps {
  placeholder: string;
  submitLabel: string;
  buildBody: (text: string) => Record<string, unknown>;
}

type ComposerMode = CaptureMode;

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

export function LaneComposer({ placeholder, submitLabel, buildBody }: LaneComposerProps) {
  const { text } = useI18n();
  const shell = useShellState();
  const mode = composerMode(buildBody);
  const title = composerTitle(mode, quickCaptureKeys(shell), text);
  // 回执 + 它的三个时钟（对账 / 超时 / 褪去）与「清理积压」按钮共用一份；stalled = 原生 P1-4 管线不 ok → 回执改口、超时不计时
  const { receipt, stalled, begin: beginReceipt } = useCaptureReceipt(mode);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ComposerError | null>(null);
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
    setNote(null);
    // 上一份捕获回执这里**不清**：它是占位卡的替身，原生 writeInboxFile 失败 / 斜杠命令都不碰 capturePending——
    // 只有下面 POST 成功那一步（beginReceipt）才替换它；失败句 / 斜杠回执按渲染栈暂时顶在它前面
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
        // 原生 submitCapture：命令成功也 `CaptureHistory.push`（"commands count too"），Composer.submit 同时把
        // historyIndex 归零——`/lang en` 之后空草稿 ↑ 能翻回它，与普通捕获成功那条路同一套账
        pushHistory(trimmed);
        setHistoryIndex(-1);
        setDraft("");
        setNote(command.note);
        return;
      }
      const response = await postAction(buildBody(trimmed));
      pushHistory(trimmed);
      setHistoryIndex(-1);
      setDraft(""); // 仅确认成功后清空（§41 草稿保留）
      beginReceipt(trimmed, response); // 成功才替换上一份回执、时钟重来（stem = server 回的 inbox 文件名，§49 对账精确键）
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
    if (e.key === "Escape") {
      // 原生 Composer.escKey 返 `.handled`：Esc 在框内就地吃掉，永不外泄到 window——FilterBar 的两段 ⎋
      // （清 ⌘F 搜索词 → 退出多选）不许因为光标在输入框里而被触发。React 17+ 的 stopPropagation 停的是
      // 原生冒泡（监听挂在 root），所以 window.addEventListener 的听众确实收不到。
      e.stopPropagation();
      // IME 候选期间的 Esc 归输入法（原生 hasMarkedText → `.ignored`）：不 blur、不 preventDefault，输入法自己撤销拼音
      if (!e.nativeEvent.isComposing) e.currentTarget.blur(); // 只交还光标，草稿不动
      return;
    }
    if (e.nativeEvent.isComposing) return;
    if (e.key === "ArrowUp" && (draft === "" || historyIndex >= 0)) {
      e.preventDefault();
      recall(1);
    } else if (e.key === "ArrowDown" && historyIndex >= 0) {
      e.preventDefault();
      recall(-1);
    }
  };

  // 原生 Composer.swift 的一行状态栈：slashError > "/" 草稿的 hintLine > 斜杠回执 > 捕获回执 >（键位提示句，D35 退役）
  // ——同一时刻只有一行；斜杠回执一改字过期后，还活着的捕获回执（或它的超时条）回到这一行
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
            setNote(null); // 斜杠回执随之过期；捕获回执**不清**——它是占位卡的替身，活到落地 / 超时（captureReceipt.ts）
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
      {note && !error && !hint ? (
        <p className="column-help">{note}</p>
      ) : receipt && !error && !hint ? (receipt.timedOut ? (
        // 原生 NoticeRow：captureTimeout = .yellow（--notice）/ raiseTimeout = .orange（--warning）
        <p className={`composer-notice is-${mode}-timeout`} role="status">{captureTimeoutNotice(mode, receipt.text, text)}</p>
      ) : (
        <p className="column-help" data-capture-receipt={mode}>{captureReceiptLine(mode, receipt.text, stalled, text)}</p>
      )) : null}
    </>
  );
}
