// 详情侧栏（BUILD-CONTRACT §2.2；D34 / CONTRACT §49 追记：卡片详情的**唯一**面）：selectedCardId 驱动，
// 「展开详情 ▸」/ ⏎ 开侧栏的绑定在卡片组件侧（cardChrome 调 openCardDetail → selectCard(id)），
// 本组件负责渲染 + 关闭（Esc/背板/按钮）+ ?card= 深链同步 + 「复制为 Markdown」（头部按钮 + 右键菜单项）。
// 挂载点：app.tsx（或 BoardPage）加一行 <DetailDrawer />——集成 agent 接线（A7 无权改
// A5/A6 的文件）。组件自身在 selectedCardId=null 时渲染 null，挂在任何页面都无副作用。
import { useEffect, useRef, useState } from "react";
import { displayId, matchesCardRef } from "../../cardId";
import { useI18n } from "../../i18n";
import { buildAppUrl, readCardId, readPage } from "../../route";
import { selectCard, useAppState } from "../../store";
import { cardToMarkdown } from "./cardMarkdown";
import { copyText } from "./copyText";
import { DetailFields, faceHeadline } from "./DetailFields";
import { DeliverableViewer } from "./DeliverableViewer";
import { FormerNames, TitleEditor } from "./TitleEditor";
import "./detail.css";

type DrawerTab = "fields" | "deliverable";

export function DetailDrawer() {
  const { text } = useI18n();
  const { selectedCardId, cardDetail, cardDetailError, board } = useAppState();
  const [tab, setTab] = useState<DrawerTab>("fields");
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [copied, setCopied] = useState(false);
  const drawerRef = useRef<HTMLDivElement>(null);
  // 打开侧栏那一刻的焦点元素（「展开详情 ▸」/ 卡片本身）——关闭时还给它；侧栏内换卡不覆盖
  const openerRef = useRef<HTMLElement | null>(null);

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

  // 换卡重置局部瞬态 + 聚焦抽屉（Esc 可达）；关闭把焦点还给打开它的控件（WAI-ARIA dialog 往返；
  // FilterPopover 同法）——除非关闭的那一下已经把焦点送去了别处，那就不抢。侧栏是 D34 后唯一的详情面，
  // 键盘用户点「展开详情 ▸」/ 在卡上按 Enter 进来、⎋ 出去，不能掉回 <body> 从页顶重新 Tab。
  useEffect(() => {
    setTab("fields");
    setMenu(null);
    setCopied(false);
    if (!selectedCardId) return undefined;
    const active = document.activeElement;
    if (active instanceof HTMLElement && active !== document.body && !drawerRef.current?.contains(active)) {
      openerRef.current = active;
    }
    drawerRef.current?.focus();
    return () => {
      // 换卡时侧栏还在（焦点仍在里面）→ 不还；真关闭时 <aside> 已卸载、焦点掉到 body → 还给 opener
      const now = document.activeElement;
      const opener = openerRef.current;
      if ((!now || now === document.body) && opener?.isConnected) opener.focus({ preventScroll: true });
    };
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
      // §60：?card= 深链可能带工作编号（用户复制看板上的 R-280）——按主键或 work_id 命中
      .find((row) => typeof row.id === "string" && matchesCardRef(row as { id: string; work_id?: unknown }, selectedCardId))
    : undefined;
  const title = (cardDetail ?? boardRow) as Record<string, unknown> | undefined;
  const heading = (typeof title?.title === "string" && title.title)
    || (typeof title?.name === "string" && title.name)
    || selectedCardId;
  // 抬头编号 = display_id（server 算好；缺席回落 selectedCardId）；主键不同才并排给出
  const shownId = title && typeof title.id === "string"
    ? displayId(title as { id: string; display_id?: unknown; work_id?: unknown })
    : selectedCardId;
  const primaryKey = title && typeof title.id === "string" ? (title.id as string) : null;

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
            <span className="zai-drawer-id">{shownId}</span>
            {primaryKey && primaryKey !== shownId && (
              <span className="zai-drawer-id zai-drawer-id-key" title={text("主键（动作/深链用）", "Primary key (actions / deep links)")}>{primaryKey}</span>
            )}
            <h2>{heading}</h2>
            {/* §37 活标题：详情已到 + 主键可用才给改名（trash/archived 行也能改，actd 侧复验）；预填 = 此刻的
                卡面标题（原生 TitleEditRow current = displaySummary，Cards.swift:1283——改名从看板上那个名字起手，
                不是抬头的冻结 title）；曾用名一行同原生 TitleEditRow（改过的旧名仍可搜索） */}
            {cardDetail && primaryKey && <TitleEditor cardId={primaryKey} current={faceHeadline(cardDetail) || heading} />}
            {cardDetail && <FormerNames titles={cardDetail.former_titles} />}
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
