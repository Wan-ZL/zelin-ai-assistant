// 卡面共用小件——镜像原生 CardSurface（mac/Sources/Cards.swift）的几样 chrome：
//   CardHead：标题行 + 右上角等宽小字 id（原生 idTag 位置）；linkify=true 时标题里的 URL 可点（Linkified，
//     原生 linkified——只给提案 / 潜在任务的摘要优先面）；
//   DetailsToggle：动作行尾右对齐的「展开详情 ▸」——打开右侧详情侧栏（openCardDetail：选中卡 + ?card= 深链）。
//     卡片详情只有这一面（D34 / issue #217，§49 追记）：原生 CardSurface 的就地展开详情槽在 web 退役，
//     卡面永远是收起态，泳道不再被撑高；
//   RelativeTime：卡面一律相对时间（19天前 / 2小时59分），hover 给绝对时间；
//   RepoChip：cwd / target basename 中性章；
//   CopyCommandLine：「单击复制指令」行——网页没有终端入口（server 无对应 endpoint），只复制，tooltip 说明；
//   ErrorLine + 让 AI 修：错误一句（红）+ 起 server 的 act.ai_fix 修复会话（POST /api/ai-fix）。
//   CardSurface（issue #8 a11y）：五种卡共用的 <article>——可聚焦、Enter/Space 打开详情侧栏
//     （「展开详情 ▸」的键盘等价物）、aria-label = 「<状态词> · <标题>」（色点 aria-hidden，状态不靠颜色）。
//     卡上没有双击绑定（D34：双击语义留给 #216 终端接管）。
//     selectable（§21 多选，原生 Kanban.swift selectableCard 的 tap catcher）：selectionMode 下点卡身 = 切换选中，
//     动作行（.card-actions）整排失效（指针穿透 + inert + capture 兜底）——误点不许批准 / 删除任何东西；勾选框留给 a11y。
//   CopiedAnnouncer：复制成功的 role=status 播报（视觉上 sr-only）——按钮文案变化 VoiceOver 不一定读。
// 纪律：颜色只用 token class；文案 text(zh,en) 内联对；不上抛 DOM event。
import { createContext, useContext, useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { postAiFix, postTerminal } from "../../api";
import { displayId, isLegacyId } from "../../cardId";
import { useI18n } from "../../i18n";
import { absoluteLabel, duration, sinceEpoch, sinceIso, useNow } from "../../relativeTime";
import { openCardDetail } from "./boardActions";
import { Linkified } from "./Linkified";
import { toggleSelected, useAppState } from "../../store";
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
  /**
   * §21 多选：这张卡可参与合并多选（原生 Kanban.swift `selectableCard(id, selectable:)`）。v0.21 起**全 lane
   * 真实卡都可选**（潜在任务 / 提案 / 运行中含排队 / 需输入 / 待验收 / 阶段性完成）——跨状态合并的合法性交给
   * 后端 merge_review 判，前端只保持选择面宽松；不可选的只有提案列 processing 占位、合并建议卡与永久性完成书立条。
   */
  selectable?: boolean;
  children: ReactNode;
}

/** 这张卡此刻在多选态里可选（selectable && store.selectionMode）：CardSurface 提供，CardHead 据此长出勾选框 */
const SelectingContext = createContext(false);

/** 多选态下卡上仍活着的控件（勾选框 / 复制指令行 / 标题里的链接 / 卡内弹窗）：点它们不算点卡身 */
const LIVE_CONTROL = 'button, a, input, textarea, select, label, summary, [role="button"], dialog';

/**
 * 卡片外壳（issue #8）：article 加 tabIndex + Enter/Space 打开详情侧栏（「展开详情 ▸」的键盘
 * 等价物；只在焦点落在卡本身、不是卡里的按钮/输入框时响应，免得抢走按钮自己的 Enter），
 * aria-label 把状态词与标题读出来（色点是 aria-hidden 的装饰）。<article> 的隐式 role 已是
 * article（可带 aria-label，VoiceOver 把整卡当一个可导航项）——不另加 role（axe aria-allowed-role 会拒）。
 * 不绑双击（D34）：双击作详情入口不可发现、还和选文本手势打架；它的语义留给 #216 终端接管。
 *
 * 多选态（原生 Kanban.swift:671-705 selectableCard 的整卡 tap catcher）：selectable 且 store.selectionMode 时
 *   · 点卡身 = 切换选中（is-selectable 指针手形；选中 is-selected accent 淡底）；
 *   · 动作行 `.card-actions` 整排失效——原生注释「the catcher deliberately blocks the card's own buttons while
 *     selecting — a mis-click must not approve/trash anything」：CSS 指针穿透之外，动作行挂 `inert`（不在 tab 序、
 *     读屏树里也没有——原生 overlay 盖住整卡，按钮本就不可达）；capture 阶段再兜底：落在动作行里的 click（含键盘
 *     Enter / Space 合成的、不认 inert 的老 WebKit）拦下、不让按钮自己的 onClick 跑，这一下改算点卡身；
 *   · 勾选框留给 a11y（原生左上角 checkmark.circle 的语义等价物），点它走自己的 onChange，不再叠一次切换。
 */
export function CardSurface({ cardId, label, className = "", selectable = false, children }: CardSurfaceProps) {
  const { selectionMode, selectedIds } = useAppState();
  const selecting = selectable && selectionMode;
  const selected = selecting && selectedIds.has(cardId);
  const ref = useRef<HTMLElement>(null);
  // 动作行由各卡自己渲染（pending 时换成一句话、结束再长回来），所以每次 render 后同步一次 inert，
  // 不只盯 selecting；React 18 没有 inert prop，直接切 attribute（React 不管它没写过的属性）
  useEffect(() => {
    ref.current?.querySelectorAll<HTMLElement>(".card-actions").forEach((row) => row.toggleAttribute("inert", selecting));
  });
  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openCardDetail(cardId);
    }
  };
  const onClickCapture = (e: MouseEvent<HTMLElement>) => {
    if (!selecting) return;
    if (!(e.target as Element).closest(".card-actions")) return;
    e.preventDefault();
    e.stopPropagation();
    toggleSelected(cardId);
  };
  const onClick = (e: MouseEvent<HTMLElement>) => {
    if (!selecting) return;
    const target = e.target as Element;
    if (target !== e.currentTarget && target.closest(LIVE_CONTROL)) return;
    toggleSelected(cardId);
  };
  const cls = ["task-card", selecting ? "is-selectable" : "", selected ? "is-selected" : "", className].filter(Boolean).join(" ");
  return (
    <SelectingContext.Provider value={selecting}>
      <article
        ref={ref}
        className={cls}
        tabIndex={0}
        aria-label={label}
        onKeyDown={onKeyDown}
        onClickCapture={onClickCapture}
        onClick={onClick}
      >
        {children}
      </article>
    </SelectingContext.Provider>
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
  /**
   * 标题里的 URL 变可点链接（原生 `linkified`，Cards.swift:1073 提案摘要 / :2028 潜在任务摘要）——
   * 只给摘要优先面；运行中 / 待验收行标题原生明确不 linkify（:1829），缺省 false
   */
  linkify?: boolean;
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
    />
  );
}

/** 标题行：可选性由外壳决定（CardSurface selectable → SelectingContext），selectionMode 下标题前长出勾选框 */
export function CardHead({ card, title, leading, isMuted = false, variant, linkify = false }: CardHeadProps) {
  const selecting = useContext(SelectingContext);
  const cls = ["card-title", variant === "lg" ? "is-lg" : "", variant === "placeholder" ? "is-placeholder" : "", isMuted ? "is-muted" : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <div className="card-head">
      {selecting && <SelectCheckbox cardId={card.id} />}
      {leading}
      <div className={cls}>{linkify ? <Linkified text={title} /> : title}</div>
      <CardIdTag card={card} />
    </div>
  );
}

/** 这张卡的详情侧栏在本会话里打开过（T2 提案「需先展开看明细」闸门读它，§54.1 第 2 项追记） */
export function useDetailViewed(cardId: string): boolean {
  const { detailViewedIds } = useAppState();
  return detailViewedIds.has(cardId);
}

/**
 * 「展开详情 ▸」（原生 CardSurface 详情槽 toggle 的字面 + 位置：plain 灰链接，动作行尾右对齐）——
 * D34 起打开右侧详情侧栏（选中卡 + ?card= 深链，可刷新还原 / 可分享），不再就地撑开卡片；
 * 关闭在侧栏自己（× / ⎋ / 背板），卡上没有「收起 ▾」。
 */
export function DetailsToggle({ cardId }: { cardId: string }) {
  const { text } = useI18n();
  return (
    <button
      type="button"
      className="card-details-toggle"
      aria-haspopup="dialog"
      onClick={() => openCardDetail(cardId)}
    >
      {text("展开详情 ▸", "Details ▸")}
    </button>
  );
}

type Stamp = { epoch?: unknown; iso?: unknown };

/** 相对时间小字（原生 RelativeTime.sinceEpoch / since）+ hover 绝对时间；解析不了不渲染 */
export function RelativeTime({ epoch, iso, prefix = "", suffix = "", className = "" }: Stamp & { prefix?: string; suffix?: string; className?: string }) {
  const { text, locale } = useI18n();
  const now = useNow();
  const label = epoch !== undefined ? sinceEpoch(epoch, now, text) : sinceIso(iso, now, text);
  if (!label) return null;
  // 前缀与时间值各自一个文本节点（原生是两个 Text 拼接；探针按节点文本逐字判「耗时 」/「验收于 」等前缀）；
  // suffix 收尾括号之类（原生「（上次检查：\(rel)）」整句一个 Text——外层 span 的 textContent 才是那一句）
  return (
    <span className={`card-meta-text${className ? ` ${className}` : ""}`} title={absoluteLabel(epoch ?? iso, locale)}>
      {prefix ? <span className="card-meta-prefix">{prefix}</span> : null}
      <span>{label}</span>
      {suffix ? <span className="card-meta-prefix">{suffix}</span> : null}
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
 * 网页：这一行本身就是复制热区，文案如实只承诺复制；tooltip 带完整命令。双击在终端接管归 #216。
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
  // 回执两句逐字镜像原生 CopyPathLine 的 launched / launchFailed（已在终端打开 / 打开终端失败）；失败另附 server 原文
  const [status, setStatus] = useState<{ msg: string; detail?: string; failed: boolean } | null>(null);
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
      setStatus({ msg: text("已在终端打开", "Opened in terminal"), failed: false });
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setStatus(null), 3000);
    } catch (e) {
      setStatus({ msg: text("打开终端失败", "Terminal launch failed"), detail: e instanceof Error ? e.message : String(e), failed: true });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button type="button" className="btn" disabled={busy} onClick={() => void open()} title={text("在终端里接管这个会话（server 推导命令）", "Take over this session in a terminal (command derived by the server)")}>
        {text("在终端接管", "Open in Terminal")}
      </button>
      {status && (
        <span className={`card-meta-text${status.failed ? " is-danger" : ""}`}>
          <span>{status.msg}</span>
          {status.detail && <span className="card-meta-detail">{` · ${status.detail}`}</span>}
        </span>
      )}
    </>
  );
}

/** 原生 AIFix.launch 的成功句（Doctor.swift）：卡片上的「让 AI 修」与依赖检查区的同名按钮共用这一句 */
export function aiFixOpenedText(text: (zh: string, en: string) => string): string {
  return text("已在 Terminal 打开修复会话——跟着 AI 走即可", "Repair session opened in Terminal — just follow the AI");
}

/**
 * 让 AI 修（原生 TaskRow.errorLine 的按钮）：POST /api/ai-fix → server 起 act.ai_fix
 * 的 Terminal 修复会话。状态行镜像原生 aiFixStatus：准备中 → 成功 4s 后淡出 / 失败红字留着。
 */
export function AiFixButton({ cardId }: { cardId: string }) {
  const { text, language } = useI18n();
  // 失败句 = 原生前缀「让 AI 修启动失败：」+ server 原文，前缀与原文各一节点（§54.4 前缀与值分两个节点）
  const [status, setStatus] = useState<{ msg: string; detail?: string; failed: boolean } | null>(null);
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
      setStatus({ msg: aiFixOpenedText(text), failed: false });
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setStatus(null), 4000);
    } catch (e) {
      setStatus({ msg: text("让 AI 修启动失败：", "Fix with AI failed to launch: "), detail: e instanceof Error ? e.message : String(e), failed: true });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button type="button" className="btn" disabled={busy} onClick={() => void launch()}>
        {text("让 AI 修", "Fix with AI")}
      </button>
      {status && (
        <span className={`card-meta-text${status.failed ? " is-danger" : ""}`}>
          <span>{status.msg}</span>
          {status.detail && <span className="card-meta-detail">{status.detail}</span>}
        </span>
      )}
    </>
  );
}

/**
 * 合并态角标（原生 Kanban.swift cardOverlay：契约七「合并分析中…」/ §21bis「合并中…」）：
 * 这张卡在某条 analyzing 的合并建议里 → 合并分析中…（backend 真态）；强制合并已提交、等下一版
 * dashboard → 合并中…（store 瞬态，回流即清）。强制合并优先（原生 mergeForcing 先判）。
 */
export function MergeStateChip({ cardId }: { cardId: string }) {
  const { text } = useI18n();
  const { board, forceMergingIds } = useAppState();
  if (forceMergingIds.has(cardId)) return <span className="chip chip-purple chip-quiet">{text("合并中…", "Merging…")}</span>;
  const analyzing = (board?.merge_suggestions ?? []).some((s) => s.status === "analyzing" && Array.isArray(s.ids) && s.ids.includes(cardId));
  if (!analyzing) return null;
  return <span className="chip chip-purple chip-quiet">{text("合并分析中…", "Analyzing…")}</span>;
}

/**
 * 错误一句（红，两行截断，hover 全文）——原生 errorLine 的文本部分；按钮由宿主放进动作行。
 * §25：`sentence` = 失败目录里这个 failure id 的人话（原生 `FailureCatalog.message(failureID).map { prefix + $0 }`），
 * 有就代替原文上卡面，原文降到 title 气泡（原生 `.help(raw)` 不论哪种都挂原文）+ 详情侧栏的「错误全文」；没有 → 原文。
 */
export function ErrorLine({ prefix, raw, sentence }: { prefix: string; raw: unknown; sentence?: string | null }) {
  if (typeof raw !== "string" || !raw) return null;
  return <p className="card-line is-danger card-error-line" title={raw}><span className="card-detail-label">{prefix}</span><span>{sentence || raw}</span></p>;
}
