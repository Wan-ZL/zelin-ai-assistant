// 潜在任务（debt/Backlog）折叠侧条（G4，BUILD-CONTRACT §2.2：「潜在任务(折叠侧条)」）。
// **§78 提案车道退役后这条侧条是机器卡的唯一收件箱**：雷达 / 每日循环 / self_improve 铸的卡
// （detected / raising 灰占位 / 落单 card_sent）全落这里，owner 在卡上点一次「促成运行」才开跑。
// 因此展开态默认 true（D80.3）——把机器卡藏在一个折叠开关后面 = 每一张雷达卡都可能没人看见。
// 收起 = 竖排窄条只显计数；展开 = 列表（吃全局过滤 chips + ⌘F 搜索）。
// 行点击 = 开详情抽屉（selectCard + ?card= 深链同步）。动词按钮属 A6 卡组件——经 renderCard
// 注入（DebtCardItem）；缺省渲染只读简行。
// §78 还把四件只在提案列挂过的东西搬了进来（它们原本的挂载点随那一列一起删了）：
//   · **快速捕获框**（LaneComposer，列头第一件，钉在列表之上）：意图 = 记一件事，发的仍是无 mode 的
//     `{action:"capture", text}`，落 `detected`；直跑框（`mode:"run"` → `approved`）留在运行中列头。
//     §34 追记 / §78 的原话：捕获框搬到潜在任务列头，⌘L 的「归提案 composer」读作「归潜在任务列的捕获框」
//     ——全局 ⌘L / ⌃⌥Space 的落点跟着它走（focusComposer.COMPOSER_SELECTOR，§54.4 2026-09-05 追记）。
//     它的 title 提示句「快速捕获（⌘L · ⌃⌥Space）」也跟着搬（§41 2026-09-04 追记 (e)，LaneComposer 自带）；
//   · §21 合并建议卡（MergeSuggestionCard，紫 accent 钉在列表顶，不进排序 / 过滤）；
//   · §44.6 静默并入回执（FoldReceiptNotices）；
//   · §21bis 强制合并 180 s 诚实超时条（ForceMergeTimeoutNotice）。
//   两条通知**跟着原来的挂载点一起搬**：原生提案列里它们住列顶 composer 槽（BoardLanes 的 `composer` prop），
//   是列表的兄弟节点、钉在列里不随卡滚走（D42）——这条侧条现在是全板最长的一列，把回执塞进滚动的
//   `.backlog-strip-list` 里当第一个孩子 = owner 滚到第 12 张卡时回执落在视口之上，活到过期都没被看见。
//   所以它们是 `.backlog-strip-list` 的兄弟、钉在捕获框之下（列内边距两边同为 6px 10px 10px，几何与那一列逐像素相同）。
//   三种通知（并入回执 / 超时条 / 合并建议卡）落地时、以及捕获提交成功时都**强制打开**这条侧条
//   （复用 store.setBacklogStripExpanded——useSubmit 的同一条路）：回执不能落在收起的条里（过滤强制展开态下
//   提交完一清过滤器，条会收回去，回执跟着没了——所以提交这一下把旗真的扳成 true）。打开之后旗归用户，
//   列头照常能收（不是 `.constant(true)` 那种锁死），但**下一条**通知到货还能再开一次。
// 展开态挂 store（store.backlogStripExpanded：换页不丢、不持久化、不进 URL——原生 Store.swift:127-128；§54.1 追记）；
// 暂缓落地 / debt 源动作超时由 useSubmit 强制打开。搜索 / 过滤命中潜在任务时**不看旗直接
// 展开**（原生 Kanban.swift:326 `searching && !debt.isEmpty ? .constant(true)`：过时的过滤器 / 收起的条永不静默藏卡），
// 此时列头开合是 no-op，清掉查询即回到旗的状态。
// 行序吃全局排序偏好（store.sortOrder，cardSort.ts）：raising 的灰占位卡钉在顶、不参与排序，也
// **不被搜索词藏起**（原生 Store.boardApprovals；一条在途提交藏到搜索后面，读起来就像捕获丢了——
// tier / 期限 / 回锅 chips 是 web 加的维度，占位卡对它们照常判定，不在此扩权）；deadline 模式可用。
// 展开态列头带原生同款「?」说明（server 目录）。
import { useEffect, useRef, type ReactNode } from "react";
import "./chrome.css";
import { sortCards, type SortOrder } from "../../cardSort";
import { domainLabel, TYPE_LABELS, useI18n } from "../../i18n";
import { buildAppUrl } from "../../route";
import { selectCard, setBacklogStripExpanded, useAppState } from "../../store";
import { cardFilterCount, matchesCardFilters } from "../../taskFilters";
import type { DebtCard } from "../../types";
import { FoldReceiptNotices, unseenFoldReceipts } from "../board/FoldReceiptNotices";
import { ForceMergeTimeoutNotice } from "../board/ForceMergeTimeoutNotice";
import { LaneHelpButton, useLaneHelp } from "../board/Lane";
import { LaneComposer } from "../board/LaneComposer";
import { MergeSuggestionCard } from "../board/MergeSuggestionCard";

export interface BacklogStripProps {
  /** A6 注入真实卡组件（含动词按钮）；不传则渲染只读简行 */
  renderCard?: (card: DebtCard) => ReactNode;
}

/** 行序（原生 visibleApprovals）：processing 灰占位钉在顶、不参与排序；其余按偏好，deadline 模式可用 */
export function orderBacklog(cards: DebtCard[], order: SortOrder): DebtCard[] {
  const placeholders = cards.filter((c) => c.processing);
  const real = cards.filter((c) => !c.processing);
  return [...placeholders, ...sortCards(real, order, (c) => (typeof c.deadline === "string" ? c.deadline : null))];
}

export function BacklogStrip({ renderCard }: BacklogStripProps) {
  const { text, language } = useI18n();
  const { board, filters, sortOrder, backlogStripExpanded, sessionIndex, forceMergeTimedOutAt } = useAppState();
  const help = useLaneHelp("debt");

  const all = board?.debt ?? [];
  // §37.2 第三层：潜在任务卡也搜会话正文（原生 Kanban.swift:341 DebtRow sessionHit）——store.sessionIndex 按 id 取。
  // 占位行（raising 的灰卡）只判 chips、不判搜索词（原生 Store.boardApprovals，§78 起这一条随占位卡搬到本条）
  const chipsOnly = { ...filters, search: "" };
  const rows = orderBacklog(
    all.filter((card) => matchesCardFilters(card, card.processing === true ? chipsOnly : filters, sessionIndex?.texts[card.id])),
    sortOrder,
  );
  const countLabel = rows.length === all.length ? `${all.length}` : `${rows.length}/${all.length}`;
  // 原生 `searching && !debt.isEmpty ? .constant(true) : $store.backlogStripExpanded`：过滤 / 搜索命中潜在任务 → 强制展开，
  // 旗不动；无命中或无过滤 → 旗说了算
  const forced = cardFilterCount(filters) > 0 && rows.length > 0;
  const expanded = forced || backlogStripExpanded;
  // §21 合并建议卡：紫 accent 钉在列表顶（analyzing/done/failed 都发，dismissed 不发）；不进排序/过滤
  const suggestions = Array.isArray(board?.merge_suggestions) ? board.merge_suggestions : [];

  // §44.6 / §21bis / §21：有没看过的并入回执、强制合并超时条、或合并建议卡落地 → 开条
  // （收起的条里的通知等于没给通知；SelectionBar 的「潜在任务条顶会出现建议卡」那句就押在这上面）。
  // 用的是 useSubmit 那条同样的 store setter，不是 `forced`：开完旗归用户，列头照常能收。
  // 记的是**逐条通知的身份**而不是一个合并出来的 bool：一条通知还活着时第二条到货，照样得能把 owner
  // 中途收起的条再打开一次；已经开过的那条则永不重开（每轮把记忆裁成当下还活着的键——过期的通知
  // 不占记忆，它日后再出现就是新的一件事）。
  const noticeKeys = [
    ...unseenFoldReceipts(board?.fold_receipts ?? []).map((r) => `receipt:${r.id}`),
    // 建议卡的判决落地（analyzing → done / failed）是新的一件事：那一刻卡上才长出「接受 / 取消」两颗键
    ...suggestions.map((s) => `suggest:${s.id}:${String(s.status)}`),
    ...(forceMergeTimedOutAt === null ? [] : [`timeout:${forceMergeTimedOutAt}`]),
  ].join(" ");   // 拼成一个字串只为当 useEffect 的依赖（键全由 id / status / 时间戳拼成，不含空格）
  const openedFor = useRef<Set<string>>(new Set());
  useEffect(() => {
    const live = noticeKeys ? noticeKeys.split(" ") : [];
    const fresh = live.filter((key) => !openedFor.current.has(key));
    openedFor.current = new Set(live);
    if (fresh.length > 0) setBacklogStripExpanded(true);
  }, [noticeKeys]);

  function openCard(id: string) {
    selectCard(id);
    window.history.replaceState(null, "", buildAppUrl(window.location.href, "board", id));
  }

  return (
    <aside className={`backlog-strip${expanded ? "" : " is-collapsed"}`}>
      <div className="backlog-strip-head">
        <button
          type="button"
          className="backlog-strip-toggle"
          aria-expanded={expanded}
          onClick={() => {
            // 强制展开期间列头是 `.constant(true)`——点了不收、也不改旗（清掉查询后回到用户原来的开合状态）
            if (!forced) setBacklogStripExpanded(!backlogStripExpanded);
          }}
        >
          <span aria-hidden="true">{expanded ? "▾" : "▸"}</span>
          <span>{text("潜在任务 · backlog", "Backlog")}</span>
          <span className="backlog-strip-count">{countLabel}</span>
        </button>
        {expanded && help && <LaneHelpButton help={help} />}
      </div>

      {/* §78：捕获框从退役的提案列头搬到这里。列表的兄弟节点、钉在条顶不随行滚走（与原生列顶输入框同款，D42）；
          提交成功 = 强制展开这条（与下面回执那条同一个 store setter） */}
      {expanded && (
        <LaneComposer
          placeholder={text("一句话，先记下来，AI 来补计划…", "One line — jot it down, the AI fills in the plan…")}
          submitLabel={text("捕获", "Capture")}
          buildBody={(t) => ({ action: "capture", text: t })}
          onSubmitted={() => setBacklogStripExpanded(true)}
        />
      )}

      {/* §44.6 并入回执（原生 LocalNotice lane .approval）+ §21bis 强制合并超时条：与捕获框同槽——
          列表的兄弟节点、钉在条顶不随行滚走（原生它们住提案列的列顶 composer 槽，D42）。
          全板最长的这一列里，进了滚动容器的回执等于没给回执：owner 滚到下面时它在视口之上，活到过期也没被看见 */}
      {expanded && (
        <>
          <FoldReceiptNotices />
          <ForceMergeTimeoutNotice />
        </>
      )}

      {expanded && (
        <div className="backlog-strip-list">
          {suggestions.map((s) => (
            <MergeSuggestionCard key={s.id} suggestion={s} />
          ))}
          {rows.length === 0 && suggestions.length === 0 && (
            <p className="trash-empty">
              {all.length === 0
                ? text("机器发现的事会先停在这里——不会自动执行，也永不过期；点「促成运行」才开跑", "Machine-filed items park here — nothing runs on its own, nothing expires; “Run it” starts one")
                : text("无匹配卡片", "No matching cards")}
            </p>
          )}
          {rows.map((card) =>
            renderCard ? (
              <div key={card.id}>{renderCard(card)}</div>
            ) : (
              <button
                key={card.id}
                type="button"
                className="backlog-row"
                onClick={() => openCard(card.id)}
              >
                <span className="backlog-row-title">{card.title}</span>
                <span className="backlog-row-meta">
                  {typeof card.type === "string" && card.type && (
                    <span className="chrome-badge">{domainLabel(TYPE_LABELS, language, card.type)}</span>
                  )}
                  {typeof card.hardness === "string" && card.hardness && (
                    <span className="chrome-badge">{card.hardness}</span>
                  )}
                  {(card.sources ?? [])
                    .map((s) => s.channel)
                    .filter((c, i, arr) => c && arr.indexOf(c) === i)
                    .map((channel) => (
                      <span key={channel}>{channel}</span>
                    ))}
                </span>
              </button>
            ),
          )}
        </div>
      )}
    </aside>
  );
}
