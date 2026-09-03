// 问问助手页（CONTRACT §27 / §54.4；?page=ask，左侧导航栏第二项）：原生 Ask.swift 的 web 版——
// 输入框 + 「提问」→ POST /api/ask（server 子进程 act.ask，一次 tool-less claude -p，≤60 s）；
// 思考态显示已耗秒数（可取消 = 丢弃回执，子进程照常结束）；答案 + 引用来源；失败 = 原文 + 「重试」；
// 「最近的问答」= GET /api/ask/history（act.ask 追加的 state/ask_history.json，最新在前）。
// AI 引擎未接入（server 回执 failure_id 指向 claude 缺席）时给「去接入（初始设置向导）」+「重新检测」。
import { useEffect, useRef, useState, type FormEvent } from "react";
import "../components/chrome/chrome.css";
import "../components/settings/settings.css";
import { fetchAskHistory, postAsk } from "../api";
import { RelativeTime } from "../components/board/cardChrome";
import { errorMessage } from "../components/settings/useToast";
import { useI18n } from "../i18n";
import { buildAppUrl } from "../route";
import type { AskAnswer, AskHistoryItem } from "../types";

const ASK_MAX_S = 60;
/** act/lib/failures.py 里「AI 引擎缺席」一族的 failure_id（claude 不在 PATH / 未登录）——给接入按钮 */
const ENGINE_MISSING = new Set(["claude_cli_missing", "claude_auth_failed", "claude_blind", "claude_cli_outdated"]);

export function isEngineMissing(answer: AskAnswer | null): boolean {
  return Boolean(answer && !answer.ok && answer.failure_id && ENGINE_MISSING.has(answer.failure_id));
}

export function AskPage() {
  const { text } = useI18n();
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState("");
  const [answer, setAnswer] = useState<AskAnswer | null>(null);
  const [history, setHistory] = useState<AskHistoryItem[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [busy, setBusy] = useState(false);
  const inflight = useRef<AbortController | null>(null);

  async function loadHistory() {
    try {
      setHistory((await fetchAskHistory()).items);
    } catch {
      /* 历史只是锦上添花：读不到就不显示 */
    }
  }

  useEffect(() => {
    void loadHistory();
  }, []);

  useEffect(() => {
    if (!busy) return undefined;
    setElapsed(0);
    const timer = window.setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);

  async function submit(q: string) {
    const trimmed = q.trim();
    if (!trimmed || busy) return;
    const controller = new AbortController();
    inflight.current = controller;
    setBusy(true);
    setAsked(trimmed);
    setAnswer(null);
    try {
      const result = await postAsk(trimmed, controller.signal);
      if (!controller.signal.aborted) {
        setAnswer(result);
        if (result.ok) {
          setQuestion("");
          void loadHistory();
        }
      }
    } catch (err) {
      if (!controller.signal.aborted) setAnswer({ ok: false, error: errorMessage(err) });
    } finally {
      if (inflight.current === controller) {
        inflight.current = null;
        setBusy(false);
      }
    }
  }

  function cancel() {
    inflight.current?.abort();
    inflight.current = null;
    setBusy(false);
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void submit(question);
  }

  return (
    <main className="settings-page ask-page">
      <a className="trash-back-link" href={buildAppUrl(window.location.href, "board", null).toString()}>{text("← 返回看板", "← Back to board")}</a>
      <div className="settings-page-head">
        <h2 className="settings-page-title">{text("问问助手", "Ask the assistant")}</h2>
        <span className="settings-helper">{text("基于产品文档与本机真实状态回答（≤150 字，注明出处；不确定就说不确定）。", "Answers from the product docs and this machine's real state (≤150 words, cites the source; says so when unsure).")}</span>
      </div>
      <form className="ask-form" onSubmit={onSubmit}>
        <input
          type="text"
          className="settings-input"
          value={question}
          disabled={busy}
          maxLength={500}
          placeholder={text("输入问题，回车提问", "Type a question, press Return")}
          aria-label={text("问题", "Question")}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <button type="submit" className="btn btn-primary" disabled={busy || !question.trim()}>{text("提问", "Ask")}</button>
      </form>
      {busy && (
        <p className="settings-helper ask-thinking" role="status">
          <span className="shell-spinner ask-spinner" aria-hidden="true" />
          {text(`思考中… 已 ${elapsed} 秒（最多 ${ASK_MAX_S} 秒）`, `Thinking… ${elapsed}s elapsed (${ASK_MAX_S}s max)`)}
          <button type="button" className="btn btn-quiet" onClick={cancel}>{text("取消", "Cancel")}</button>
        </p>
      )}
      {answer?.ok && (
        <section className="ask-answer" aria-label={text("回答", "Answer")}>
          <p className="ask-question">{asked}</p>
          <p className="ask-answer-text">{answer.answer}</p>
          {answer.citation && <p className="settings-helper">{text("出处：", "Source: ")}{answer.citation}</p>}
        </section>
      )}
      {answer && !answer.ok && (
        <div className="settings-warning ask-failure" role="alert">
          <p>{isEngineMissing(answer) ? text("AI 引擎未连接——先接入 AI 引擎才能提问。", "The AI engine is not connected — connect it first to ask questions.") : (answer.error ?? text("失败", "Failed"))}</p>
          <div className="settings-actions">
            <button type="button" className="btn" onClick={() => void submit(asked)}>{text("重试", "Retry")}</button>
            {isEngineMissing(answer) && (
              <>
                <a className="btn" href={buildAppUrl(window.location.href, "setup", null).toString()}>{text("去接入（初始设置向导）", "Connect (setup wizard)")}</a>
                <button type="button" className="btn" onClick={() => void submit(asked)}>{text("重新检测", "Re-detect")}</button>
              </>
            )}
          </div>
        </div>
      )}
      <section className="settings-section" aria-labelledby="ask-history-title">
        <h3 id="ask-history-title" className="settings-section-title">{text("最近的问答", "Recent questions")}</h3>
        {history.length === 0
          ? <p className="settings-helper">{text("还没有问过问题。", "No questions yet.")}</p>
          : (
            <ul className="settings-list ask-history">
              {history.map((item, index) => (
                <li key={`${item.ts ?? ""}-${index}`} className="settings-list-row">
                  <span className="settings-list-title">{item.q}</span>
                  <p className="settings-list-desc ask-history-answer">{item.a}</p>
                  <span className="settings-list-meta">
                    {item.citation && <span>{item.citation}</span>}
                    {item.ts && <RelativeTime iso={item.ts} prefix=" · " />}
                  </span>
                </li>
              ))}
            </ul>
          )}
      </section>
    </main>
  );
}
