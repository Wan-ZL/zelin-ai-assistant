// 过滤 chip 条 + ⌘F 搜索（G4，BUILD-CONTRACT §2.2）。挂载点 = shell 的 searchSlot
// （HeaderBar 中缝槽位，app.tsx 注入 <AppShell searchSlot={<FilterBar />}>）。
// 状态：store.filters 唯一真源，URL query 唯一持久化（taskFilters.ts）；挂载时水合深链。
// 匹配语义见 taskFilters.ts 头注释——A6 各列用 matchesCardFilters(row, filters) 消费。
import { useEffect, useRef, useState } from "react";
import "./chrome.css";
import {
  CHANNEL_LABELS,
  domainLabel,
  TYPE_LABELS,
  useI18n,
  type LabelTable,
} from "../../i18n";
import { clearFilters, initFiltersFromUrl, setFilters, useAppState } from "../../store";
import {
  cardFilterCount,
  collectChannels,
  collectTypes,
  toggleFilterValue,
  type DeadlineFilter,
} from "../../taskFilters";
import { TaskPropertyPicker, type TaskPropertyOption } from "./TaskPropertyPicker";

type ChipKey = "tier" | "type" | "channel" | "deadline";

const TIER_VALUES = ["T0", "T1", "T2"] as const;

export function FilterBar() {
  const { text, language } = useI18n();
  const { board, filters } = useAppState();
  const [openChip, setOpenChip] = useState<ChipKey | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    initFiltersFromUrl(); // 深链进场：?tier=…&q=… 水合进 store
  }, []);

  useEffect(() => {
    // ⌘F（mac）/ Ctrl+F 聚焦搜索框——接管浏览器查找（看板数据全在客户端）
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "f") {
        event.preventDefault();
        searchRef.current?.focus();
        searchRef.current?.select();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const activeCount = cardFilterCount(filters);
  const openFor = (key: ChipKey) => (open: boolean) => setOpenChip(open ? key : null);

  // 多选维度的 chip 触发内容：名称 + 已选摘要（≤2 个原样列出，更多显计数）
  function chipContent(name: string, selected: string[], table?: LabelTable) {
    if (!selected.length) return <span>{name}</span>;
    const labels = selected.map((v) => (table ? domainLabel(table, language, v) : v));
    const summary = labels.length <= 2 ? labels.join(", ") : String(labels.length);
    return (
      <>
        <span>{name}</span>
        <span className="chrome-chip-count">· {summary}</span>
      </>
    );
  }

  function multiOptions(values: string[], table?: LabelTable): TaskPropertyOption<string>[] {
    return values.map((value) => ({
      value,
      label: table ? domainLabel(table, language, value) : value,
    }));
  }

  const typeValues = collectTypes(board);
  const channelValues = collectChannels(board);

  const deadlineOptions: TaskPropertyOption<DeadlineFilter>[] = [
    { value: "all", label: text("全部期限", "Any deadline") },
    { value: "has", label: text("有期限", "Has deadline") },
    { value: "soon", label: text("7 天内到期", "Due within 7 days") },
    { value: "overdue", label: text("已逾期", "Overdue") },
    { value: "none", label: text("无期限", "No deadline") },
  ];
  const deadlineLabel = deadlineOptions.find((o) => o.value === filters.deadline)?.label ?? "";

  return (
    <div className="chrome-filterbar" role="toolbar" aria-label={text("过滤与搜索", "Filter and search")}>
      <input
        ref={searchRef}
        className="chrome-search"
        type="search"
        placeholder={text("搜索（⌘F）", "Search (⌘F)")}
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

      {typeValues.length > 0 && (
        <TaskPropertyPicker
          value={filters.types[0] ?? ""}
          selectedValues={filters.types}
          options={multiOptions(typeValues, TYPE_LABELS)}
          open={openChip === "type"}
          onOpenChange={openFor("type")}
          onChange={(value) => setFilters({ types: toggleFilterValue(filters.types, value) })}
          triggerClassName={`chrome-chip${filters.types.length ? " is-active" : ""}`}
          triggerContent={chipContent(text("类型", "Type"), filters.types, TYPE_LABELS)}
          ariaLabel={text("按类型过滤", "Filter by type")}
        />
      )}

      {channelValues.length > 0 && (
        <TaskPropertyPicker
          value={filters.channels[0] ?? ""}
          selectedValues={filters.channels}
          options={multiOptions(channelValues, CHANNEL_LABELS)}
          open={openChip === "channel"}
          onOpenChange={openFor("channel")}
          onChange={(value) => setFilters({ channels: toggleFilterValue(filters.channels, value) })}
          triggerClassName={`chrome-chip${filters.channels.length ? " is-active" : ""}`}
          triggerContent={chipContent(text("渠道", "Channel"), filters.channels, CHANNEL_LABELS)}
          ariaLabel={text("按渠道过滤", "Filter by channel")}
        />
      )}

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
    </div>
  );
}
