// 列顶输入框（提案列快速捕获 / 运行中列直跑）。§41 网页纪律：
//   - IME 回车守卫：拼音候选未上屏时的 Enter（isComposing）不提交；
//   - 草稿保留：仅在 server 确认成功后清空输入框，失败时草稿原样留着；
//   - payload 由调用方经 buildBody(text) 构造（propose = {action:"capture",text}，
//     direct-run = {action:"capture",text,mode:"run"}——多一个字段 server 400）。
import { useState } from "react";
import type { KeyboardEvent } from "react";
import { postAction } from "../../api";
import { useI18n } from "../../i18n";
import { describeActionError } from "./boardActions";

interface LaneComposerProps {
  placeholder: string;
  submitLabel: string;
  /** 提交成功后输入框下方的一次性回执文案（如「已提交，AI 分析中…」） */
  successNote: string;
  buildBody: (text: string) => Record<string, unknown>;
}

export function LaneComposer({ placeholder, submitLabel, successNote, buildBody }: LaneComposerProps) {
  const { text } = useI18n();
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const submit = async () => {
    const trimmed = draft.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    setSent(false);
    try {
      await postAction(buildBody(trimmed));
      setDraft(""); // 仅确认成功后清空（§41 草稿保留）
      setSent(true);
    } catch (e) {
      setError(describeActionError(e, text));
    } finally {
      setBusy(false);
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    // IME 守卫：组合输入中的 Enter 是「上屏」不是「提交」
    if (e.key === "Enter" && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void submit();
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
      {error && <p className="composer-error">{error}</p>}
      {sent && !error && <p className="column-help">{successNote}</p>}
    </>
  );
}
