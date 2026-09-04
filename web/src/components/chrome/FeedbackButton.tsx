// 顶栏「提建议」（§29；原生 header 按钮的 web 版）：打开 FeedbackDialog，ids=[]（对整体）；
// 发出后不做乐观更新，只给一行回执（feedback 不进看板列，没有回流可等）。
// 顶栏 tight 档（§49 追记 2026-09-04）只留 💡 图标，「提建议」进 aria-label / title。
// 回执小条 portal 到 body、fixed 挂在按钮下方（FilterPopover 同法），**不是 .chrome-filterbar 的子元素**：
// 条 nowrap、又是顶栏 grid 给槽位的 max-content 下限（shell.css），server 原文错误句长度不可预算，
// 进条就会把标题挤掉、把右翼推出视口、整页横向滚动 4 s。
import { useLayoutEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { postAction } from "../../api";
import { useI18n } from "../../i18n";
import { useHeaderDensity } from "../shell/headerDensity";
import { describeActionError } from "../board/boardActions";
import { FeedbackDialog } from "../board/FeedbackDialog";

export function FeedbackButton() {
  const { text } = useI18n();
  const density = useHeaderDensity();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  const send = async (body: Record<string, unknown>) => {
    setOpen(false);
    try {
      await postAction(body);
      setNote(text("已记录建议，感谢", "Feedback recorded"));   // 原生 Store.swift LocalNotice 同句
    } catch (e) {
      setNote(describeActionError(e, text));
    }
    window.setTimeout(() => setNote(null), 4000);
  };

  return (
    <>
      {density === "tight" ? (
        <button
          ref={buttonRef}
          type="button"
          className="chrome-icon-button"
          onClick={() => setOpen(true)}
          aria-label={text("提建议", "Send feedback")}
          title={text("给维护者提建议", "Send feedback to the maintainer")}
        >
          {/* lightbulb（原生 💡 提建议） */}
          <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" fill="none"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.6 10.8c.6.5 1 1.2 1.1 2l.1.7h4.8l.1-.7c.1-.8.5-1.5 1.1-2A6 6 0 0 0 12 3Z" />
          </svg>
        </button>
      ) : (
        <button ref={buttonRef} type="button" className="chrome-select-toggle" onClick={() => setOpen(true)} title={text("给维护者提建议", "Send feedback to the maintainer")}>
          {text("提建议", "Send feedback")}
        </button>
      )}
      {note && <FeedbackNote anchorRef={buttonRef} note={note} />}
      {open && <FeedbackDialog ids={[]} onSubmit={(body) => void send(body)} onCancel={() => setOpen(false)} />}
    </>
  );
}

// 回执小条：fixed，左沿对齐按钮、贴视口边裁（FilterPopover 的定位算法）；宽度在 chrome.css 封顶、字内可折。
// 视口变化就重量一次——回执只活 4 s，不值得像 popover 那样关掉。
function FeedbackNote({ anchorRef, note }: { anchorRef: RefObject<HTMLButtonElement | null>; note: string }) {
  const pillRef = useRef<HTMLSpanElement>(null);
  const [position, setPosition] = useState({ left: 0, top: 0 });

  useLayoutEffect(() => {
    function place() {
      const anchor = anchorRef.current;
      const pill = pillRef.current;
      if (!anchor || !pill) return;
      const anchorRect = anchor.getBoundingClientRect();
      const pillRect = pill.getBoundingClientRect();
      const gap = 4;
      const edge = 8;
      const left = Math.max(edge, Math.min(anchorRect.left, window.innerWidth - pillRect.width - edge));
      setPosition({ left, top: Math.max(edge, anchorRect.bottom + gap) });
    }
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [anchorRef, note]);

  return createPortal(
    <span
      ref={pillRef}
      className="chrome-feedback-note"
      role="status"
      style={{ position: "fixed", left: position.left, top: position.top }}
    >
      {note}
    </span>,
    document.body,
  );
}
