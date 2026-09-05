// 过滤 chip 条 + ⌘F 搜索（G4，BUILD-CONTRACT §2.2；chip 只剩 Tier / 期限 / 回锅——类型 / 渠道
// 两维 2026-09-04 owner 决策 D28 退役）。挂载点 = shell 的 searchSlot
// （HeaderBar 中缝槽位，app.tsx 注入 <AppShell searchSlot={<FilterBar />}>）。
// 状态：store.filters 唯一真源，URL query 唯一持久化（taskFilters.ts）；挂载时水合深链。
// 匹配语义见 taskFilters.ts 头注释——A6 各列用 matchesCardFilters(row, filters) 消费。
// 顶栏三档密度（§49 追记 2026-09-04，档位来自 HeaderDensityContext）——纯展示，过滤 / 排序 / 多选状态一个字不变：
//   full    搜索框 + chips + 清除 + 提建议 + 选择 + 排序 全部行内（今天的样子）；
//   compact chips / 排序 / 清除 / 选择 收进「筛选 · N」按钮的 popover（FilterPopover），搜索框留在条上；
//   tight   搜索框折成放大镜（点它 / ⌘F 展开，⎋ / 失焦收起，有词时带点），「筛选」只留图标 + 计数，提建议只留图标。
import { useCallback, useEffect, useRef, useState, type PointerEvent } from "react";
import "./chrome.css";
import { useI18n } from "../../i18n";
import { normalizeSortOrder, SORT_ORDERS, type SortOrder } from "../../cardSort";
import { clearFilters, initFiltersFromUrl, setFilters, setSelectionMode, setSortOrder, useAppState } from "../../store";
import { useHeaderDensity } from "../shell/headerDensity";
import { FeedbackButton } from "./FeedbackButton";
import { FilterPopover } from "./FilterPopover";
import { cardFilterCount, toggleFilterValue, type DeadlineFilter } from "../../taskFilters";
import { TaskPropertyPicker, type TaskPropertyOption } from "./TaskPropertyPicker";

type ChipKey = "tier" | "deadline";

const TIER_VALUES = ["T0", "T1", "T2"] as const;

function FunnelIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 5h18l-7 8v6l-4 2v-8L3 5Z" />
    </svg>
  );
}

function MagnifierIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="M15.5 15.5 21 21" />
    </svg>
  );
}

/** 不承载文字的 <input> 类型：多选态卡上的勾选框等——焦点在它们上面时 ⎋ 仍归看板（退出多选） */
const NON_TEXT_INPUT_TYPES = new Set(["button", "submit", "reset", "checkbox", "radio", "range", "color", "file", "image", "hidden"]);

/** ⎋ 的目标是不是「别人的」文字输入框（列顶输入框、回收站 / 永久性完成的搜索、标题编辑、contenteditable……）。
 *  原生 Kanban.swift:186 把 escClearSearch 只挂在搜索 TextField 上、Composer.escKey 自己吃 Esc（返 .handled）——
 *  别的输入框里按 ⎋ 从来到不了看板层。web 的 window 监听没有这层天然作用域，这里补回来：只有 ⌘F 搜索框
 *  本身（`ownField`）与非输入元素的 ⎋ 才算看板的。 */
export function escapeBelongsToForeignField(target: EventTarget | null, ownField: HTMLElement | null): boolean {
  if (!(target instanceof HTMLElement) || target === ownField) return false;
  if (target instanceof HTMLTextAreaElement) return true;
  if (target instanceof HTMLInputElement) return !NON_TEXT_INPUT_TYPES.has(target.type);
  // 可编辑区里的任何子节点都算（属性可继承）；不用 isContentEditable——jsdom 没实现，浏览器与判例要走同一条路
  return target.closest('[contenteditable]:not([contenteditable="false"])') !== null;
}

export function FilterBar() {
  const { text } = useI18n();
  const density = useHeaderDensity();
  const { filters, sortOrder, selectionMode } = useAppState();
  const [openChip, setOpenChip] = useState<ChipKey | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const panelButtonRef = useRef<HTMLButtonElement>(null);

  const inline = density === "full"; // chips 直接摆在条上；否则收进 popover
  const tight = density === "tight";
  const searchVisible = !tight || searchOpen; // tight：默认只剩放大镜
  const hasSearch = filters.search.trim() !== "";
  const closePanel = useCallback(() => setPanelOpen(false), []);

  useEffect(() => {
    initFiltersFromUrl(); // 深链进场：?tier=…&q=… 水合进 store
  }, []);

  // 档位回到 full → popover 没有触发按钮了，收掉；离开 tight → 搜索展开态没意义，清掉
  useEffect(() => {
    if (inline) setPanelOpen(false);
    if (!tight) setSearchOpen(false);
  }, [inline, tight]);

  // popover 关了就把里面开着的 listbox 一并复位（下次打开不带旧定位）
  useEffect(() => {
    if (!panelOpen) setOpenChip(null);
  }, [panelOpen]);

  // tight：放大镜展开成输入框后聚焦（⌘F 与点击共用这一条路）
  useEffect(() => {
    if (tight && searchOpen) {
      searchRef.current?.focus();
      searchRef.current?.select();
    }
  }, [tight, searchOpen]);

  useEffect(() => {
    // ⌘F（mac）/ Ctrl+F 聚焦搜索框——接管浏览器查找（看板数据全在客户端）；任何档位都通
    // ⎋（原生 Kanban.swift:98 契约七 + escClearSearch 分两段）：有搜索词先清词；已空 → 退出多选；弹窗 / 筛选面板 / 详情侧栏
    // 开着时不插手（面板自己吃 ⎋ 关自己；侧栏是 <aside role=dialog aria-modal>，D34 后是唯一详情面、⎋ 是它的正式关法——
    // 那一下只关侧栏，不顺手清词 / 退多选；tight 的搜索框失焦即收起，所以第二下 ⎋ 的 blur 也就是「收起」）。
    // 作用域（§15 2026-09-05 追记，原生 Kanban.swift:186 / :225-236）：① IME 候选期间的 ⎋（isComposing / keyCode 229）
    // 归输入法——撤销一串拼音不许顺手把整个搜索词抹掉（原生 hasMarkedText → .ignored，IME 红线）；② 光标在别的文字输入框
    // 里（列顶输入框、回收站搜索、标题编辑……）时 ⎋ 归那个框——原生 escClearSearch 只挂在搜索 TextField 上，
    // Composer 自己吃 Esc；window 监听没有这层作用域，按 target 补回来。
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "f") {
        event.preventDefault();
        setPanelOpen(false);
        if (tight && !searchOpen) {
          setSearchOpen(true); // 聚焦交给上面的 effect（输入框这一帧还没渲染）
          return;
        }
        searchRef.current?.focus();
        searchRef.current?.select();
        return;
      }
      if (event.key !== "Escape") return;
      if (event.isComposing || event.keyCode === 229) return; // IME 红线：候选期间的 ⎋ 归输入法
      if (escapeBelongsToForeignField(event.target, searchRef.current)) return; // 别人的输入框，⎋ 归它
      if (panelOpen || document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return;
      if (filters.search.trim()) {
        setFilters({ search: "" });
        return;
      }
      if (searchRef.current && document.activeElement === searchRef.current) {
        searchRef.current.blur();
        return;
      }
      if (selectionMode) setSelectionMode(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [filters.search, selectionMode, panelOpen, tight, searchOpen]);

  const activeCount = cardFilterCount(filters);
  // 「筛选」角标只数面板里的维度——搜索词有自己的入口（放大镜上的点）
  const panelCount = activeCount - Number(hasSearch);
  const openFor = (key: ChipKey) => (open: boolean) => setOpenChip(open ? key : null);

  // 多选维度的 chip 触发内容：名称 + 已选摘要（≤2 个原样列出，更多显计数）
  function chipContent(name: string, selected: string[]) {
    if (!selected.length) return <span>{name}</span>;
    const summary = selected.length <= 2 ? selected.join(", ") : String(selected.length);
    return (
      <>
        <span>{name}</span>
        <span className="chrome-chip-count">· {summary}</span>
      </>
    );
  }

  function multiOptions(values: string[]): TaskPropertyOption<string>[] {
    return values.map((value) => ({ value, label: value }));
  }

  const deadlineOptions: TaskPropertyOption<DeadlineFilter>[] = [
    { value: "all", label: text("全部期限", "Any deadline") },
    { value: "has", label: text("有期限", "Has deadline") },
    { value: "soon", label: text("7 天内到期", "Due within 7 days") },
    { value: "overdue", label: text("已逾期", "Overdue") },
    { value: "none", label: text("无期限", "No deadline") },
  ];
  const deadlineLabel = deadlineOptions.find((o) => o.value === filters.deadline)?.label ?? "";

  // 卡片排序偏好（原生 Settings「卡片排序」Picker 的三个选项，文案逐字；store 持久化 cardSortOrder）
  const sortLabels: Record<SortOrder, string> = {
    newest: text("新的在上（默认）", "Newest first"),
    oldest: text("旧的在上（先清积压）", "Oldest first"),
    deadline: text("Deadline 近的在上", "Deadline first"),
  };

  const filtersLabel = text("筛选", "Filters");

  // tight 且搜索框展开着：点条上别的控件时先别让它抢焦点——否则 mousedown 的 blur 先把搜索框收成放大镜、
  // 居中的图标行重排，mouseup 落在空处，第一下点击就丢了（WKWebView 点按钮不给焦点，blur.relatedTarget
  // 靠不住）。收起搜索框交给「筛选」的 click 顺手做（与开面板同一次渲染，面板量到的是收起后的锚点位置）；
  // 提建议开的是居中 modal，showModal 夺焦点时 blur 自然收起。
  function keepSearchFocus(event: PointerEvent<HTMLDivElement>) {
    if (tight && searchOpen && event.target !== searchRef.current) event.preventDefault();
  }

  const chips = (
    <>
      <TaskPropertyPicker
        value={filters.tiers[0] ?? ""}
        selectedValues={filters.tiers}
        options={multiOptions([...TIER_VALUES])}
        open={openChip === "tier"}
        onOpenChange={openFor("tier")}
        onChange={(value) => setFilters({ tiers: toggleFilterValue(filters.tiers, value) })}
        triggerClassName={`chrome-chip${filters.tiers.length ? " is-active" : ""}`}
        triggerContent={chipContent("Tier", filters.tiers)}
        ariaLabel={text("按 tier 过滤", "Filter by tier")}
      />

      <TaskPropertyPicker
        value={filters.deadline}
        options={deadlineOptions}
        open={openChip === "deadline"}
        onOpenChange={openFor("deadline")}
        onChange={(value) => setFilters({ deadline: value })}
        triggerClassName={`chrome-chip${filters.deadline !== "all" ? " is-active" : ""}`}
        triggerContent={
          filters.deadline === "all"
            ? <span>{text("期限", "Deadline")}</span>
            : <><span>{text("期限", "Deadline")}</span><span className="chrome-chip-count">· {deadlineLabel}</span></>
        }
        ariaLabel={text("按期限过滤", "Filter by deadline")}
      />

      <button
        type="button"
        className={`chrome-chip${filters.reraisedOnly ? " is-active" : ""}`}
        aria-pressed={filters.reraisedOnly}
        onClick={() => setFilters({ reraisedOnly: !filters.reraisedOnly })}
      >
        {text("↩︎ 回锅", "↩︎ Re-raised")}
      </button>
    </>
  );

  const clearButton = activeCount > 0 && (
    <button
      type="button"
      className="chrome-chip-clear"
      onClick={() => {
        clearFilters();
        setPanelOpen(false);
      }}
    >
      {text(`清除（${activeCount}）`, `Clear (${activeCount})`)}
    </button>
  );

  // §21 多选入口（原生 header「选择」）：切进 selectionMode，卡上长出勾选框、底部出操作条
  const selectToggle = (
    <button
      type="button"
      className={`chrome-select-toggle${selectionMode ? " is-on" : ""}`}
      aria-pressed={selectionMode}
      onClick={() => {
        setSelectionMode(!selectionMode);
        setPanelOpen(false);
      }}
    >
      {selectionMode ? text("退出选择", "Done selecting") : text("选择", "Select")}
    </button>
  );

  const sortControl = (
    <label className="chrome-sort">
      <span className="chrome-sort-label">{text("排序", "Sort")}</span>
      <select
        className="chrome-sort-select"
        value={sortOrder}
        aria-label={text("卡片排序", "Card sorting")}
        onChange={(event) => setSortOrder(normalizeSortOrder(event.target.value))}
      >
        {SORT_ORDERS.map((order) => (
          <option key={order} value={order}>{sortLabels[order]}</option>
        ))}
      </select>
    </label>
  );

  return (
    <div
      className="chrome-filterbar"
      role="toolbar"
      aria-label={text("过滤与搜索", "Filter and search")}
      onPointerDownCapture={keepSearchFocus}
    >
      {searchVisible ? (
        <input
          ref={searchRef}
          className={`chrome-search${tight ? " is-expanded" : ""}`}
          type="search"
          placeholder={text("搜索卡片（⌘F）", "Search cards (⌘F)")}
          aria-label={text("搜索卡片", "Search cards")}
          value={filters.search}
          onChange={(event) => setFilters({ search: event.target.value })}
          onBlur={tight ? () => setSearchOpen(false) : undefined}
        />
      ) : (
        <button
          type="button"
          className={`chrome-icon-button chrome-search-toggle${hasSearch ? " is-active" : ""}`}
          aria-label={text("搜索卡片", "Search cards")}
          title={text("搜索 ⌘F", "Search ⌘F")}
          onClick={() => setSearchOpen(true)}
        >
          <MagnifierIcon />
          {hasSearch && <span className="chrome-search-dot" aria-hidden="true" />}
        </button>
      )}

      {inline ? (
        <>
          {chips}
          {clearButton}
          {/* §29 全局提建议（原生 header「提建议」）：ids=[] 的 feedback 动作 */}
          <FeedbackButton />
          {selectToggle}
          {sortControl}
        </>
      ) : (
        <>
          <button
            ref={panelButtonRef}
            type="button"
            className={`chrome-filter-button${panelCount > 0 ? " is-active" : ""}${tight ? " is-icon" : ""}`}
            aria-haspopup="dialog"
            aria-expanded={panelOpen}
            aria-label={filtersLabel}
            title={panelCount > 0 ? `${filtersLabel} · ${panelCount}` : filtersLabel}
            onClick={() => {
              setPanelOpen((open) => !open);
              setSearchOpen(false);
            }}
          >
            <FunnelIcon />
            {!tight && <span>{filtersLabel}</span>}
            {panelCount > 0 && <span className="chrome-filter-badge">{tight ? panelCount : `· ${panelCount}`}</span>}
          </button>
          <FeedbackButton />
          {panelOpen && (
            <FilterPopover anchorRef={panelButtonRef} ariaLabel={filtersLabel} onClose={closePanel}>
              <div className="chrome-filter-panel-row">{chips}</div>
              <div className="chrome-filter-panel-row">{sortControl}</div>
              <div className="chrome-filter-panel-footer">
                {selectToggle}
                {clearButton}
              </div>
            </FilterPopover>
          )}
        </>
      )}
    </div>
  );
}
