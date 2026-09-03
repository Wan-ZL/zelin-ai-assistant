// 列顶输入框（提案列快速捕获 / 运行中列直跑）。§41 网页纪律：
//   - IME 回车守卫：拼音候选未上屏时的 Enter（isComposing）不提交；
//   - 草稿保留：仅在 server 确认成功后清空输入框，失败时草稿原样留着；
//   - payload 由调用方经 buildBody(text) 构造（propose = {action:"capture",text}，
//     direct-run = {action:"capture",text,mode:"run"}——多一个字段 server 400）。
//   - 历史 ↑/↓（最近 20 条，localStorage）与斜杠命令 /rec /lang /open（composerCommands.ts，
//     原生 Store.swift / Composer.swift 同款，s4 1.8）——命令不发 inbox，只给一行回执。
import { useState } from "react";
import type { KeyboardEvent } from "react";
import { postAction } from "../../api";
import { useI18n } from "../../i18n";
import { describeActionError } from "./boardActions";
import { pushHistory, readHistory, runSlashCommand } from "./composerCommands";

interface LaneComposerProps {
  placeholder: string;
  submitLabel: string;
  /** 提交成功后输入框下方的一次性回执文案（如「已提交，AI 分析中…」） */
  successNote: string;
  buildBody: (text: string) => Record<string, unknown>;
}

/** 失败一行（原生 slashError）：前缀句 + 细节各一节点——「提交失败，已保留输入」/「未识别或参数错误：」+ 原文 */
type ComposerError = { prefix: string; detail?: string };

export function LaneComposer({ placeholder, submitLabel, successNote, buildBody }: LaneComposerProps) {
  const { text } = useI18n();
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ComposerError | null>(null);
  const [sent, setSent] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [historyIndex, setHistoryIndex] = useState(-1); // -1 = 不在翻历史

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

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    // IME 守卫：组合输入中的 Enter 是「上屏」不是「提交」
    if (e.key === "Enter" && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void submit();
    } else if (e.key === "ArrowUp" && (draft === "" || historyIndex >= 0)) {
      e.preventDefault();
      recall(1);
    } else if (e.key === "ArrowDown" && historyIndex >= 0) {
      e.preventDefault();
      recall(-1);
    }
  };

  return (
    <>
      <div className="lane-composer">
        <input
          type="text"
          value={draft}
          placeholder={placeholder}
          disabled={busy}
          onChange={(e) => {
            setDraft(e.target.value);
            setSent(false);
          }}
          onKeyDown={onKeyDown}
        />
        <button
          type="button"
          className="btn btn-primary"
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
      {sent && !error && <p className="column-help">{successNote}</p>}
      {note && !error && <p className="column-help">{note}</p>}
    </>
  );
}
