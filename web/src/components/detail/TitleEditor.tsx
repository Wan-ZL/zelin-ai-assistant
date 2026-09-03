// 活标题改名（§37 set_title；原生 Cards.swift 行内编辑）：详情抽屉抬头的铅笔 → 输入框 → ⏎ 提交
// {action:"set_title", id, title}（server 归一空白、1..64 code points 才发；这里只做同款前置校验给即时反馈）。
// 回流后 display_title 变、former_titles 追加——无乐观更新（useSubmit 回流即回执）。
import { useState, type KeyboardEvent } from "react";
import { useI18n } from "../../i18n";
import { clipCodePoints, useSubmit } from "../board/boardActions";

export const TITLE_MAX = 64;

/** §37 客户端归一：所有空白 run 折成单空格 + trim；返回 null = 不合法（空 / 超长） */
export function normalizeTitle(raw: string): string | null {
  const title = raw.split(/\s+/).filter(Boolean).join(" ");
  if (!title) return null;
  return [...title].length <= TITLE_MAX ? title : null;
}

export function TitleEditor({ cardId, current }: { cardId: string; current: string }) {
  const { text } = useI18n();
  const { pending, error, submit } = useSubmit();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(current);

  const commit = async () => {
    const title = normalizeTitle(draft);
    if (!title || title === current) {
      setEditing(false);
      return;
    }
    const ok = await submit({ action: "set_title", id: cardId, title });
    if (ok) setEditing(false);
  };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void commit();
    } else if (e.key === "Escape") {
      e.stopPropagation();
      setEditing(false);
      setDraft(current);
    }
  };

  if (!editing) {
    return (
      <span className="zai-title-edit">
        <button type="button" className="zai-detail-copy" disabled={pending} onClick={() => { setDraft(current); setEditing(true); }} aria-label={text("改名", "Rename")}>
          {pending ? text("改名中…", "Renaming…") : text("✎ 改名", "✎ Rename")}
        </button>
        {error && <span className="zai-detail-callout zai-detail-callout--danger">{error}</span>}
      </span>
    );
  }
  const valid = normalizeTitle(draft) !== null;
  return (
    <span className="zai-title-edit is-editing">
      <input
        className="zai-title-input"
        type="text"
        value={draft}
        autoFocus
        aria-label={text("新标题", "New title")}
        onChange={(e) => setDraft(clipCodePoints(e.target.value, TITLE_MAX * 2))}
        onKeyDown={onKey}
      />
      <span className="zai-detail-dim">{[...draft.trim()].length}/{TITLE_MAX}</span>
      <button type="button" className="btn btn-primary" disabled={!valid} onClick={() => void commit()}>{text("保存", "Save")}</button>
      <button type="button" className="btn" onClick={() => { setEditing(false); setDraft(current); }}>{text("取消", "Cancel")}</button>
    </span>
  );
}
