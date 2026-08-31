// 详情抽屉（BUILD-CONTRACT §2.2）：selectedCardId 驱动，双击/⏎ 开抽屉的绑定在卡片
// 组件侧（A6 调 selectCard(id)），本组件负责渲染 + 关闭（Esc/背板/按钮）+ ?card= 深链
// 同步 + 「复制为 Markdown」（头部按钮 + 右键菜单项）。
// 挂载点：app.tsx（或 BoardPage）加一行 <DetailDrawer />——集成 agent 接线（A7 无权改
// A5/A6 的文件）。组件自身在 selectedCardId=null 时渲染 null，挂在任何页面都无副作用。
import { useEffect, useRef, useState } from "react";
import { useI18n } from "../../i18n";
import { buildAppUrl, readCardId, readPage } from "../../route";
import { selectCard, useAppState } from "../../store";
import { cardToMarkdown } from "./cardMarkdown";
import { copyText } from "./copyText";
import { DetailFields } from "./DetailFields";
import { DeliverableViewer } from "./DeliverableViewer";
import "./detail.css";

type DrawerTab = "fields" | "deliverable";

export function DetailDrawer() {
  const { text } = useI18n();
  const { selectedCardId, cardDetail, cardDetailError, board } = useAppState();
  const [tab, setTab] = useState<DrawerTab>("fields");
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [copied, setCopied] = useState(false);
  const drawerRef = useRef<HTMLDivElement>(null);

  // 初载深链恢复：?card=R-101 → selectCard（A8 接手整页路由后可移走，这里幂等）
  useEffect(() => {
    const linked = readCardId(window.location.search);
    if (linked) selectCard(linked);
  }, []);

  // 抽屉开合 ↔ URL 同步（CONVENTIONS：只经 route.ts，replaceState 不进历史栈）
  useEffect(() => {
    const url = buildAppUrl(window.location.href, readPage(window.location.search), selectedCardId);
    window.history.replaceState(null, "", url);
  }, [selectedCardId]);

  // 换卡重置局部瞬态 + 聚焦抽屉（Esc 可达）
  useEffect(() => {
    setTab("fields");
    setMenu(null);
    setCopied(false);
    if (selectedCardId) drawerRef.current?.focus();
  }, [selectedCardId]);

  useEffect(() => {
    if (!selectedCardId) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (menu) setMenu(null);
      else selectCard(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedCardId, menu]);

  if (!selectedCardId) return null;

  // 详情在途时用投影行占位标题（board 里找得到就先显示）
  const boardRow = board
    ? (["needs_approval", "running", "needs_input", "review", "completed", "debt", "trash"] as const)
      .flatMap((section) => (Array.isArray(board[section]) ? (board[section] as Array<Record<string, unknown>>) : []))
      .find((row) => row.id === selectedCardId)
    : undefined;
  const title = (cardDetail ?? boardRow) as Record<string, unknown> | undefined;
  const heading = (typeof title?.title === "string" && title.title)
    || (typeof title?.name === "string" && title.name)
    || selectedCardId;

  const onCopyMarkdown = () => {
    if (!cardDetail) return;
    void copyText(cardToMarkdown(cardDetail, text)).then((ok) => {
      setCopied(ok);
      if (ok) window.setTimeout(() => setCopied(false), 1500);
    });
    setMenu(null);
  };

  return (
    <div className="zai-drawer-root">
      {/* 背板点击 = 关抽屉（与 Esc 同语义） */}
      <div className="zai-drawer-backdrop" onClick={() => selectCard(null)} />
      <aside
        ref={drawerRef}
        className="zai-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={text(`卡片详情 ${selectedCardId}`, `Card detail ${selectedCardId}`)}
        tabIndex={-1}
        onContextMenu={(event) => {
          // 右键菜单：复制为 Markdown（保留浏览器默认菜单给文本选区——有选区时不拦）
          if (window.getSelection()?.toString()) return;
          event.preventDefault();
          setMenu({ x: event.clientX, y: event.clientY });
        }}
        onClick={() => menu && setMenu(null)}
      >
        <header className="zai-drawer-header">
          <div className="zai-drawer-heading">
            <span className="zai-drawer-id">{selectedCardId}</span>
            <h2>{heading}</h2>
          </div>
          <div className="zai-drawer-tools">
            <button type="button" className="zai-detail-copy" onClick={onCopyMarkdown} disabled={!cardDetail}>
              {copied ? text("已复制", "Copied") : text("复制为 Markdown", "Copy as Markdown")}
            </button>
            <button type="button" className="zai-drawer-close" onClick={() => selectCard(null)} aria-label={text("关闭", "Close")}>
              ×
            </button>
          </div>
        </header>

        <nav className="zai-drawer-tabbar" role="tablist" aria-label={text("详情页签", "Detail tabs")}>
          <button type="button" role="tab" aria-selected={tab === "fields"}
            className={`zai-drawer-tabbtn${tab === "fields" ? " is-active" : ""}`} onClick={() => setTab("fields")}>
            {text("详情", "Details")}
          </button>
          <button type="button" role="tab" aria-selected={tab === "deliverable"}
            className={`zai-drawer-tabbtn${tab === "deliverable" ? " is-active" : ""}`} onClick={() => setTab("deliverable")}>
            {text("交付物", "Deliverable")}
          </button>
        </nav>

        <div className="zai-drawer-body">
          {cardDetailError && (
            <p className="zai-detail-callout zai-detail-callout--danger">{cardDetailError}</p>
          )}
          {!cardDetail && !cardDetailError && <p className="zai-detail-dim">{text("加载详情…", "Loading detail…")}</p>}
          {cardDetail && (tab === "fields" ? <DetailFields detail={cardDetail} /> : <DeliverableViewer detail={cardDetail} />)}
        </div>

        {menu && (
          <div className="zai-drawer-menu" style={{ left: menu.x, top: menu.y }} role="menu">
            <button type="button" role="menuitem" onClick={onCopyMarkdown} disabled={!cardDetail}>
              {text("复制为 Markdown", "Copy as Markdown")}
            </button>
          </div>
        )}
      </aside>
    </div>
  );
}
