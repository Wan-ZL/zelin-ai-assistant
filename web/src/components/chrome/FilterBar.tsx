// 过滤 chip 条 + ⌘F 搜索（G4，BUILD-CONTRACT §2.2；chip 只剩 Tier / 期限 / 回锅——类型 / 渠道
// 两维 2026-09-04 owner 决策 D28 退役）。挂载点 = shell 的 searchSlot
// （HeaderBar 中缝槽位，app.tsx 注入 <AppShell searchSlot={<FilterBar />}>）。
// 状态：store.filters 唯一真源，URL query 唯一持久化（taskFilters.ts）；挂载时水合深链。
// 匹配语义见 taskFilters.ts 头注释——A6 各列用 matchesCardFilters(row, filters) 消费。
import { useEffect, useRef, useState } from "react";
import "./chrome.css";
import { useI18n } from "../../i18n";
import { normalizeSortOrder, SORT_ORDERS, type SortOrder } from "../../cardSort";
import { clearFilters, initFiltersFromUrl, setFilters, setSelectionMode, setSortOrder, useAppState } from "../../store";
import { FeedbackButton } from "./FeedbackButton";
import { cardFilterCount, toggleFilterValue, type DeadlineFilter } from "../../taskFilters";
import { TaskPropertyPicker, type TaskPropertyOption } from "./TaskPropertyPicker";

type ChipKey = "tier" | "deadline";

const TIER_VALUES = ["T0", "T1", "T2"] as const;

export function FilterBar() {
  const { text } = useI18n();
  const { filters, sortOrder, selectionMode } = useAppState();
  const [openChip, setOpenChip] = useState<ChipKey | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    initFiltersFromUrl(); // 深链进场：?tier=…&q=… 水合进 store
  }, []);

  useEffect(() => {
    // ⌘F（mac）/ Ctrl+F 聚焦搜索框——接管浏览器查找（看板数据全在客户端）
    // ⎋（原生 Kanban.swift:98 契约七 + escClearSearch 分两段）：有搜索词先清词；已空 → 退出多选；弹窗开着时不插手
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "f") {
        event.preventDefault();
        searchRef.current?.focus();
        searchRef.current?.select();
        return;
      }
      if (event.key === "Escape" && !document.querySelector("dialog[open]")) {
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
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [filters.search, selectionMode]);

  const activeCount = cardFilterCount(filters);
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

  return (
    <div className="chrome-filterbar" role="toolbar" aria-label={text("过滤与搜索", "Filter and search")}>
      <input
        ref={searchRef}
        className="chrome-search"
        type="search"
        placeholder={text("搜索卡片（⌘F）", "Search cards (⌘F)")}
        aria-label={text("搜索卡片", "Search cards")}
        value={filters.search}
        onChange={(event) => setFilters({ search: event.target.value })}
      />

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

      {activeCount > 0 && (
        <button
          type="button"
          className="chrome-chip-clear"
          onClick={() => clearFilters()}
        >
          {text(`清除（${activeCount}）`, `Clear (${activeCount})`)}
        </button>
      )}

      {/* §29 全局提建议（原生 header「提建议」）：ids=[] 的 feedback 动作 */}
      <FeedbackButton />
      {/* §21 多选入口（原生 header「选择」）：切进 selectionMode，卡上长出勾选框、底部出操作条 */}
      <button
        type="button"
        className={`chrome-select-toggle${selectionMode ? " is-on" : ""}`}
        aria-pressed={selectionMode}
        onClick={() => setSelectionMode(!selectionMode)}
      >
        {selectionMode ? text("退出选择", "Done selecting") : text("选择", "Select")}
      </button>

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
    </div>
  );
}
