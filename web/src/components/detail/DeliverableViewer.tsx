// 交付物 tab：final_draft（chat 交付成稿）+ 从详情字段发现的 deliverables/ 文件。
// 安全红线（BUILD-CONTRACT §2.2）：HTML 交付物一律 <iframe sandbox="allow-scripts">
// ——绝不加 allow-same-origin（脚本可跑但拿不到父页 origin/DOM/fetch 面）；
// markdown 走本目录的 escape-first MarkdownDocument。「在访达中显示」→ POST /api/reveal，
// 路径推导在 server 端（客户端只传 card_id）。
import { useEffect, useState } from "react";
import { ApiError, deliverableUrl, postReveal } from "../../api";
import { useI18n } from "../../i18n";
import type { CardDetail } from "../../types";
import { extractDeliverables, fetchDeliverableText, looksLikeHtml, type DeliverableRef } from "./deliverables";
import { MarkdownDocument } from "./MarkdownDocument";

interface Entry {
  key: string;
  label: string;
  render:
    | { mode: "markdown-inline"; value: string }
    | { mode: "html-inline"; value: string }
    | { mode: "file"; ref: DeliverableRef };
}

function FileBody({ cardId, fileRef }: { cardId: string; fileRef: DeliverableRef }) {
  const { text } = useI18n();
  const url = deliverableUrl(cardId, fileRef.name);
  const [fetched, setFetched] = useState<{ name: string; text?: string; error?: string } | null>(null);

  const wantsText = fileRef.kind === "markdown" || fileRef.kind === "text";
  useEffect(() => {
    if (!wantsText) return undefined;
    const controller = new AbortController();
    setFetched(null);
    fetchDeliverableText(cardId, fileRef.name, controller.signal).then(
      (body) => setFetched({ name: fileRef.name, text: body }),
      (error: unknown) => {
        if (!controller.signal.aborted) setFetched({ name: fileRef.name, error: String(error) });
      },
    );
    return () => controller.abort();
  }, [cardId, fileRef.name, wantsText]);

  if (fileRef.kind === "html") {
    return (
      <iframe
        className="zai-deliverable-frame"
        title={fileRef.name}
        src={url}
        // 红线：只 allow-scripts；绝不 allow-same-origin（否则 iframe 内脚本同源拿到 /api 面）
        sandbox="allow-scripts"
      />
    );
  }
  if (fileRef.kind === "image") {
    return <img className="zai-deliverable-image" src={url} alt={fileRef.name} />;
  }
  if (wantsText) {
    if (!fetched || fetched.name !== fileRef.name) return <p className="zai-detail-dim">{text("加载中…", "Loading…")}</p>;
    if (fetched.error != null || fetched.text == null) {
      return <p className="zai-detail-callout zai-detail-callout--danger">{text("读取交付物失败：", "Failed to load deliverable: ")}{fetched.error}</p>;
    }
    return fileRef.kind === "markdown"
      ? <MarkdownDocument value={fetched.text} />
      : <pre className="zai-deliverable-text">{fetched.text}</pre>;
  }
  return (
    <p>
      {text("此类型不支持内嵌预览。", "No inline preview for this file type.")}{" "}
      <a href={url} target="_blank" rel="noreferrer">{text("在新标签页打开", "Open in a new tab")}</a>
    </p>
  );
}

export interface DeliverableViewerProps {
  detail: CardDetail;
}

export function DeliverableViewer({ detail }: DeliverableViewerProps) {
  const { text } = useI18n();
  const cardId = detail.id;
  const finalDraft = typeof detail.final_draft === "string" && detail.final_draft.trim() !== "" ? detail.final_draft : null;

  const entries: Entry[] = [];
  if (finalDraft) {
    entries.push({
      key: "final_draft",
      label: text("成稿", "Final draft"),
      // §33：final_draft 可能被 harvest 回填成整页 HTML——srcdoc iframe 沙箱展示
      render: looksLikeHtml(finalDraft)
        ? { mode: "html-inline", value: finalDraft }
        : { mode: "markdown-inline", value: finalDraft },
    });
  }
  for (const ref of extractDeliverables(detail)) {
    entries.push({ key: `file:${ref.name}`, label: ref.name, render: { mode: "file", ref } });
  }

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const selected = entries.find((entry) => entry.key === selectedKey) ?? entries[0] ?? null;

  const [reveal, setReveal] = useState<{ state: "idle" | "busy" | "done" | "error"; message?: string }>({ state: "idle" });
  const onReveal = () => {
    setReveal({ state: "busy" });
    postReveal(cardId).then(
      () => {
        setReveal({ state: "done" });
        window.setTimeout(() => setReveal((current) => (current.state === "done" ? { state: "idle" } : current)), 2000);
      },
      (error: unknown) => {
        setReveal({ state: "error", message: error instanceof ApiError ? error.message : String(error) });
      },
    );
  };

  return (
    <div className="zai-deliverable">
      <div className="zai-deliverable-toolbar">
        <div className="zai-deliverable-tabs" role="tablist" aria-label={text("交付物列表", "Deliverables")}>
          {entries.map((entry) => (
            <button
              key={entry.key}
              type="button"
              role="tab"
              aria-selected={selected?.key === entry.key}
              className={`zai-deliverable-tab${selected?.key === entry.key ? " is-active" : ""}`}
              onClick={() => setSelectedKey(entry.key)}
            >
              {entry.label}
            </button>
          ))}
        </div>
        <button type="button" className="zai-detail-copy" onClick={onReveal} disabled={reveal.state === "busy"}>
          {reveal.state === "busy" ? text("定位中…", "Revealing…")
            : reveal.state === "done" ? text("已在访达中定位", "Revealed in Finder")
              : text("在访达中显示", "Reveal in Finder")}
        </button>
      </div>
      {reveal.state === "error" && (
        <p className="zai-detail-callout zai-detail-callout--danger">{text("访达定位失败：", "Reveal failed: ")}{reveal.message}</p>
      )}

      {selected === null ? (
        <p className="zai-detail-dim">
          {text("此卡暂无可预览的交付物（repo 交付看目标仓库的 PR/分支；文件型交付可用上方按钮在访达中定位）。",
            "No previewable deliverable on this card (repo deliveries live in the target repo; use the button above to reveal files in Finder).")}
        </p>
      ) : selected.render.mode === "markdown-inline" ? (
        <MarkdownDocument value={selected.render.value} />
      ) : selected.render.mode === "html-inline" ? (
        <iframe
          className="zai-deliverable-frame"
          title={selected.label}
          srcDoc={selected.render.value}
          sandbox="allow-scripts"
        />
      ) : (
        <FileBody cardId={cardId} fileRef={selected.render.ref} />
      )}
    </div>
  );
}
