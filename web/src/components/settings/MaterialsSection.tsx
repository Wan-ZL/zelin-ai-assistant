// 设置页 section「素材库」（CONTRACT §62，owner 决策 D11 / R2.5）。
// 一个输入入口（链接 + 一行备注 → POST /api/materials/add）+ 一个按钮打开小弹窗，弹窗只列 server
// 已按 status=open 过滤的条目（尚未开 PR / 完成 / 放弃），可滚动、每行可「放弃」。
// 不铸卡、不做 Slack：条目只进 state/materials 台账，由每日循环（P5）消费。
// 数据经 store（refreshMaterials / addMaterial / dismissMaterial）；这里只存草稿 + 弹窗开关 + toast。
import { useEffect, useState } from "react";
import { ApiError } from "../../api";
import { useI18n } from "../../i18n";
import { absoluteLabel, sinceIso, useNow } from "../../relativeTime";
import { addMaterial, dismissMaterial, refreshMaterials, useAppState } from "../../store";
import type { MaterialItem } from "../../types";
import { ModalDialog } from "../board/ModalDialog";

const TOAST_MS = 6000;

interface Toast {
  kind: "ok" | "error";
  message: string;
}

/** 状态 chip 文案（wire 词表 §62.3；未知值原样展示——add-only 纪律） */
function statusLabel(status: string, text: (zh: string, en: string) => string): string {
  switch (status) {
    case "new": return text("新", "New");
    case "picked_up": return text("循环已读取", "Picked up by the loop");
    case "proposal_created": return text("已生成提案", "Proposal created");
    case "pr_opened": return text("已开 PR", "PR opened");
    case "done": return text("完成", "Done");
    case "dismissed": return text("已放弃", "Dismissed");
    default: return status;
  }
}

export function MaterialsSection() {
  const { text, locale } = useI18n();
  const { materials, materialsError } = useAppState();
  const [url, setUrl] = useState("");
  const [note, setNote] = useState("");
  const [isAdding, setAdding] = useState(false);
  const [isOpen, setOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);

  useEffect(() => {
    void refreshMaterials();
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(timer);
  }, [toast]);

  const canAdd = (url.trim() !== "" || note.trim() !== "") && !isAdding;
  const openCount = materials?.counts.open ?? null;

  async function add() {
    setAdding(true);
    setToast(null);
    try {
      await addMaterial({ url: url.trim(), note: note.trim() });
      setUrl("");
      setNote("");
      setToast({ kind: "ok", message: text("已加入素材库，每日循环会读到它。", "Added — the daily loop will pick it up.") });
    } catch (error) {
      setToast({ kind: "error", message: error instanceof ApiError ? error.message : String(error) });
    } finally {
      setAdding(false);
    }
  }

  async function dismiss(item: MaterialItem) {
    setBusyId(item.id);
    try {
      await dismissMaterial(item.id);
    } catch (error) {
      setToast({ kind: "error", message: error instanceof ApiError ? error.message : String(error) });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="settings-section" aria-labelledby="settings-materials-title">
      <h3 id="settings-materials-title" className="settings-section-title">{text("素材库", "Materials box")}</h3>
      <p className="settings-helper">
        {text(
          "看到好东西就往这里扔：一个链接（YouTube、文章、repo…）加一句为什么值得看。不会变成卡片；每日自我改进循环会抓取内容、结合本产品提出改进提案。",
          "Drop things worth learning from here: a link (YouTube, article, repo…) plus one line on why. Nothing becomes a card; the daily self-improvement loop fetches the content and turns it into proposals for this product.",
        )}
      </p>

      <form
        className="materials-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (canAdd) void add();
        }}
      >
        <input
          className="settings-input materials-input"
          type="text"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          aria-label={text("链接", "Link")}
          placeholder={text("https://…（可空，只写备注也行）", "https://… (optional — a note alone is fine)")}
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          disabled={isAdding}
        />
        <input
          className="settings-input materials-input"
          type="text"
          aria-label={text("一行备注", "One-line note")}
          placeholder={text("为什么值得看 / 想借鉴什么", "Why it matters / what to borrow")}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          disabled={isAdding}
          maxLength={2000}
        />
        <button type="submit" className="btn btn-primary" disabled={!canAdd}>
          {isAdding ? text("加入中…", "Adding…") : text("加入", "Add")}
        </button>
      </form>

      <div className="settings-actions">
        <button
          type="button"
          className="btn"
          onClick={() => setOpen(true)}
          disabled={materials === null && materialsError === null}
        >
          {openCount === null
            ? text("查看待处理", "Show pending")
            : text(`查看待处理（${openCount}）`, `Show pending (${openCount})`)}
        </button>
        <span className="settings-helper">
          {text("只列尚未开 PR 的条目；开了 PR / 完成 / 放弃的自动消失。", "Lists only items without a PR yet; anything with a PR, done or dismissed drops off.")}
        </span>
      </div>

      {materialsError && (
        <p className="settings-error" role="alert">{materialsError}</p>
      )}

      {isOpen && (
        <ModalDialog title={text("素材库 · 待处理", "Materials box · pending")} onCancel={() => setOpen(false)}>
          <MaterialsList
            items={materials?.items ?? []}
            busyId={busyId}
            onDismiss={(item) => void dismiss(item)}
            statusLabel={(status) => statusLabel(status, text)}
            locale={locale}
          />
          <div className="dialog-actions">
            <button type="button" className="btn" onClick={() => setOpen(false)}>
              {text("关闭", "Close")}
            </button>
          </div>
        </ModalDialog>
      )}

      {toast && (
        <div className={`settings-toast is-${toast.kind}`} role={toast.kind === "error" ? "alert" : "status"}>
          {toast.message}
        </div>
      )}
    </section>
  );
}

interface MaterialsListProps {
  items: MaterialItem[];
  busyId: string | null;
  onDismiss: (item: MaterialItem) => void;
  statusLabel: (status: string) => string;
  locale: string;
}

function MaterialsList({ items, busyId, onDismiss, statusLabel, locale }: MaterialsListProps) {
  const { text } = useI18n();
  const now = useNow();
  if (items.length === 0) {
    return <p className="dialog-body">{text("空的——扔点东西进来。", "Empty — drop something in.")}</p>;
  }
  return (
    <ul className="materials-list" aria-label={text("待处理素材", "Pending materials")}>
      {items.map((item) => (
        <li key={item.id} className="materials-item">
          <div className="materials-item-main">
            {item.note && <div className="materials-item-note">{item.note}</div>}
            {item.url && (
              <a className="materials-item-url" href={item.url} target="_blank" rel="noopener noreferrer">
                {item.url}
              </a>
            )}
            <div className="materials-item-meta">
              <span className="materials-item-status">{statusLabel(item.status)}</span>
              {item.links.proposal_id && <span className="materials-item-link">{item.links.proposal_id}</span>}
              <span title={absoluteLabel(item.created_at, locale)}>{sinceIso(item.created_at, now, text) ?? item.created_at}</span>
            </div>
          </div>
          <button
            type="button"
            className="btn btn-danger materials-dismiss"
            disabled={busyId === item.id}
            aria-label={text(`放弃 ${item.note || item.url}`, `Dismiss ${item.note || item.url}`)}
            onClick={() => onDismiss(item)}
          >
            {text("放弃", "Dismiss")}
          </button>
        </li>
      ))}
    </ul>
  );
}
