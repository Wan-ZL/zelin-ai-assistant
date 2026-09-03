// 卡面共用小件——镜像原生 CardSurface（mac/Sources/Cards.swift）的几样 chrome：
//   CardHead：标题行 + 右上角等宽小字 id（原生 idTag 位置，收起态可见）；
//   DetailsToggle：动作行尾右对齐的「展开详情 ▸ / 收起 ▾」（展开态记在 store，会话内按卡 id 记忆）；
//   RelativeTime：卡面一律相对时间（19天前 / 2小时59分），hover 给绝对时间；
//   RepoChip：cwd / target basename 中性章；
//   CopyCommandLine：「单击复制指令」行——网页没有终端入口（server 无对应 endpoint），只复制，tooltip 说明；
//   ErrorLine + 让 AI 修：错误一句（红）+ 起 server 的 act.ai_fix 修复会话（POST /api/ai-fix）。
//   CardSurface（issue #8 a11y）：五种卡共用的 <article>——可聚焦、Enter/Space 打开详情抽屉
//     （双击的键盘等价物）、aria-label = 「<状态词> · <标题>」（色点 aria-hidden，状态不靠颜色）。
//   CopiedAnnouncer：复制成功的 role=status 播报（视觉上 sr-only）——按钮文案变化 VoiceOver 不一定读。
// 纪律：颜色只用 token class；文案 text(zh,en) 内联对；不上抛 DOM event。
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { postAiFix, postTerminal } from "../../api";
import { displayId, isLegacyId } from "../../cardId";
import { useI18n } from "../../i18n";
import { absoluteLabel, duration, sinceEpoch, sinceIso, useNow } from "../../relativeTime";
import { openCardDetail } from "./boardActions";
import { toggleCardExpanded, toggleSelected, useAppState } from "../../store";
import { copyText } from "../detail/copyText";

/** id 标签读的投影键（§60 两段式编号：卡面展示 display_id，动作回传仍送主键 id） */
export type CardIdRow = { id: string; display_id?: unknown; work_id?: unknown; id_kind?: unknown };

/** 卡片 id 标签（右上角等宽小字）：展示 displayId()；legacy R 主键（检测即分号的旧卡）灰显 */
export function CardIdTag({ card }: { card: CardIdRow }) {
  return <span className={isLegacyId(card) ? "card-id card-id-legacy" : "card-id"}>{displayId(card)}</span>;
}

interface CardSurfaceProps {
  cardId: string;
  /** VoiceOver 读的一句：状态词 + 标题（例「执行中 · 修 flaky 测试」）——状态不靠色点 */
  label: string;
  className?: string;
  children: ReactNode;
}

/**
 * 卡片外壳（issue #8）：原生 CardSurface 的整卡双击开详情在网页上没有键盘等价物——
 * 这里给 article 加 tabIndex + Enter/Space 打开详情抽屉（只在焦点落在卡本身、不是
 * 卡里的按钮/输入框时响应，免得抢走按钮自己的 Enter），aria-label 把状态词与标题
 * 读出来（色点是 aria-hidden 的装饰）。<article> 的隐式 role 已是 article（可带 aria-label，
 * VoiceOver 把整卡当一个可导航项）——不另加 role（axe aria-allowed-role 会拒）。
 */
export function CardSurface({ cardId, label, className = "", children }: CardSurfaceProps) {
  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openCardDetail(cardId);
    }
  };
  return (
    <article
      className={`task-card${className ? ` ${className}` : ""}`}
      tabIndex={0}
      aria-label={label}
      onDoubleClick={() => openCardDetail(cardId)}
      onKeyDown={onKeyDown}
    >
      {children}
    </article>
  );
}

/** 复制成功的屏幕阅读器播报（视觉隐藏；aria-live=polite 不打断正在读的内容） */
export function CopiedAnnouncer({ copied }: { copied: boolean }) {
  const { text } = useI18n();
  return <span className="sr-only" role="status">{copied ? text("已复制到剪贴板", "Copied to clipboard") : ""}</span>;
}

interface CardHeadProps {
  card: CardIdRow;
  title: string;
  /** 标题前的小图标/色点（原生 TaskRow 的 Circle、DebtRow 的 tray 图标） */
  leading?: ReactNode;
  isMuted?: boolean;
  /**
   * 标题字级（tokens.css type-scale）：缺省 = 行标题 12 medium（TaskRow/ReviewRow/DebtRow）；
   * "lg" = 提案卡摘要 15 semibold（ApprovalCardView）；"placeholder" = AI 研究中占位 13 regular 次级
   */
  variant?: "lg" | "placeholder";
  /** §21 多选：可选卡（提案 / 运行中 / 待验收）在 selectionMode 下标题前长出勾选框 */
  selectable?: boolean;
}

/** §21 多选勾选框（原生 Kanban「选择」态的每卡 checkbox）；只在 store.selectionMode 下渲染 */
export function SelectCheckbox({ cardId }: { cardId: string }) {
  const { text } = useI18n();
  const { selectionMode, selectedIds } = useAppState();
  if (!selectionMode) return null;
  return (
    <input
      type="checkbox"
      className="card-select"
      aria-label={text(`选择 ${cardId}`, `Select ${cardId}`)}
      checked={selectedIds.has(cardId)}
      onChange={() => toggleSelected(cardId)}
      onDoubleClick={(event) => event.stopPropagation()}
    />
  );
}

export function CardHead({ card, title, leading, isMuted = false, variant, selectable = false }: CardHeadProps) {
  const cls = ["card-title", variant === "lg" ? "is-lg" : "", variant === "placeholder" ? "is-placeholder" : "", isMuted ? "is-muted" : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <div className="card-head">
      {selectable && <SelectCheckbox cardId={card.id} />}
      {leading}
      <div className={cls}>{title}</div>
      <CardIdTag card={card} />
    </div>
  );
}

/** 一张卡是否展开（store 会话内记忆） */
export function useCardExpanded(cardId: string): boolean {
  const { expandedCardIds } = useAppState();
  return expandedCardIds.has(cardId);
}

/** 展开详情 ▸ / 收起 ▾（原生 CardSurface 详情槽的 toggle：plain 灰链接，动作行尾右对齐） */
export function DetailsToggle({ cardId }: { cardId: string }) {
  const { text } = useI18n();
  const expanded = useCardExpanded(cardId);
  return (
    <button
      type="button"
      className="card-details-toggle"
      aria-expanded={expanded}
      onClick={() => toggleCardExpanded(cardId)}
    >
      {expanded ? text("收起 ▾", "Collapse ▾") : text("展开详情 ▸", "Details ▸")}
    </button>
  );
}

/** 详情区容器（只在展开时渲染；children 由各卡按原生 detailBlock 组织） */
export function CardDetails({ cardId, children }: { cardId: string; children: ReactNode }) {
  const expanded = useCardExpanded(cardId);
  if (!expanded) return null;
  return <div className="card-details">{children}</div>;
}

type Stamp = { epoch?: unknown; iso?: unknown };

/** 相对时间小字（原生 RelativeTime.sinceEpoch / since）+ hover 绝对时间；解析不了不渲染 */
export function RelativeTime({ epoch, iso, prefix = "", className = "" }: Stamp & { prefix?: string; className?: string }) {
  const { text, locale } = useI18n();
  const now = useNow();
  const label = epoch !== undefined ? sinceEpoch(epoch, now, text) : sinceIso(iso, now, text);
  if (!label) return null;
  // 前缀与时间值各自一个文本节点（原生是两个 Text 拼接；探针按节点文本逐字判「耗时 」/「验收于 」等前缀）
  return (
    <span className={`card-meta-text${className ? ` ${className}` : ""}`} title={absoluteLabel(epoch ?? iso, locale)}>
      {prefix ? <span className="card-meta-prefix">{prefix}</span> : null}
      <span>{label}</span>
    </span>
  );
}

/** 两个 epoch 之间的时长（原生 RelativeTime.duration）；to 缺省 = 现在（「已等待验收」自驱走表） */
export function DurationText({ from, to, prefix = "" }: { from: unknown; to?: unknown; prefix?: string }) {
  const { text, locale } = useI18n();
  const now = useNow();
  const end = to === undefined ? now / 1000 : to;
  const label = duration(from, end, text);
  if (!label) return null;
  const title = absoluteLabel(from, locale);
  return (
    <span className="card-meta-text" title={title}>
      {prefix ? <span className="card-meta-prefix">{prefix}</span> : null}
      <span>{label}</span>
    </span>
  );
}

/** cwd / target 的 basename 中性章（原生 Badge(lastPathComponent, .secondary)） */
export function RepoChip({ path }: { path: unknown }) {
  if (typeof path !== "string" || !path) return null;
  const base = path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || path;
  return <span className="chip" title={path}>{base}</span>;
}

/**
 * 「单击复制指令」行（原生 TaskRow / ReviewRow 的 copy 仰赖整卡点击 + 双击起终端）。
 * 网页：整卡双击已归详情抽屉，且 server 没有终端 endpoint——这一行本身就是复制热区，
 * 文案如实只承诺复制；tooltip 带完整命令。
 */
export function CopyCommandLine({ cmd }: { cmd: unknown }) {
  const { text } = useI18n();
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  if (typeof cmd !== "string" || !cmd) return null;
  return (
    <>
      <button
        type="button"
        className={`card-copy-line${copied ? " is-copied" : ""}`}
        title={cmd}
        onClick={() => {
          void copyText(cmd).then((ok) => {
            if (!ok) return;
            setCopied(true);
            if (timer.current) clearTimeout(timer.current);
            timer.current = setTimeout(() => setCopied(false), 1500);
          });
        }}
      >
        {copied
          ? text("已复制 ✓", "Copied ✓")
          : text("单击复制指令 · 粘贴到终端即可接管会话", "Click to copy the command · paste it in a terminal to take over the session")}
      </button>
      <CopiedAnnouncer copied={copied} />
    </>
  );
}

/**
 * 「在终端接管」（原生 双击指令行 → TerminalLauncher 的 web 落点，§68.7）：POST /api/terminal →
 * server 从投影行推导命令、写 .command 并 open（Terminal.app 执行）。客户端只传 card_id。
 * 非 darwin / 无会话时 server 报 501 / 400，原文显示。
 */
export function TerminalButton({ cardId }: { cardId: string }) {
  const { text } = useI18n();
  const [status, setStatus] = useState<{ msg: string; failed: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const open = async () => {
    if (busy) return;
    setBusy(true);
    setStatus(null);
    try {
      await postTerminal(cardId);
      setStatus({ msg: text("已在 Terminal 打开", "Opened in Terminal"), failed: false });
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setStatus(null), 3000);
    } catch (e) {
      setStatus({ msg: e instanceof Error ? e.message : String(e), failed: true });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button type="button" className="btn" disabled={busy} onClick={() => void open()} title={text("在 Terminal 里接管这个会话（server 推导命令）", "Take over this session in Terminal (command derived by the server)")}>
        {text("在终端接管", "Open in Terminal")}
      </button>
      {status && <span className={`card-meta-text${status.failed ? " is-danger" : ""}`}>{status.msg}</span>}
    </>
  );
}

/**
 * 让 AI 修（原生 TaskRow.errorLine 的按钮）：POST /api/ai-fix → server 起 act.ai_fix
 * 的 Terminal 修复会话。状态行镜像原生 aiFixStatus：准备中 → 成功 4s 后淡出 / 失败红字留着。
 */
export function AiFixButton({ cardId }: { cardId: string }) {
  const { text, language } = useI18n();
  const [status, setStatus] = useState<{ msg: string; failed: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const launch = async () => {
    if (busy) return;
    setBusy(true);
    setStatus({ msg: text("正在准备诊断包…", "Preparing the diagnostic bundle…"), failed: false });
    try {
      await postAiFix(cardId, language);
      setStatus({ msg: text("已在 Terminal 打开修复会话——跟着 AI 走即可", "Repair session opened in Terminal — just follow the AI"), failed: false });
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setStatus(null), 4000);
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e);
      setStatus({ msg: text("让 AI 修启动失败：", "Fix with AI failed to launch: ") + detail, failed: true });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button type="button" className="btn" disabled={busy} onClick={() => void launch()}>
        {text("让 AI 修", "Fix with AI")}
      </button>
      {status && <span className={`card-meta-text${status.failed ? " is-danger" : ""}`}>{status.msg}</span>}
    </>
  );
}

/** 错误一句（红，两行截断，hover 全文）——原生 errorLine 的文本部分；按钮由宿主放进动作行 */
export function ErrorLine({ prefix, raw }: { prefix: string; raw: unknown }) {
  if (typeof raw !== "string" || !raw) return null;
  return <p className="card-line is-danger card-error-line" title={raw}><span className="card-detail-label">{prefix}</span><span>{raw}</span></p>;
}
