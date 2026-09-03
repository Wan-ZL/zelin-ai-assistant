// Slack「监控范围」的频道 / 成员勾选器（原生 SettingsSlack.swift pickers / pickerList，§68.1 追记；标签逐字镜像）：
// 「加载频道和成员」（有数据后变「刷新」= 绕过 1 h 缓存；忙态「加载中…」）→ GET /api/slack/directory；两张清单
// （频道（@你 才建卡）/ 关注的人（第一位 = 你的 manager））各带「筛选…」框、勾选即写目录的 list 字段
// （slack_channels 存 channel id、watch_people 存 handle——PUT /api/settings/slack，与原生 toggleChannel /
// togglePerson 同一落点；已勾的浮到顶、最多列 200 项）。目录失败句：act 侧的双语 message 原文；解释器起不来 =
// 原生「找不到可用的 python（」+ 原句 + 「）」（前缀独立节点）；保存失败 = 「保存设置失败: 」+ 原句。
import { useMemo, useState } from "react";
import { fetchSlackDirectory } from "../../api";
import { useI18n } from "../../i18n";
import { saveSettingsSection, useAppState } from "../../store";
import type { SlackDirEntry, SlackDirectory } from "../../types";
import { errorMessage } from "./useToast";

const LIST_CAP = 200;

type Text = (zh: string, en: string) => string;

/** 目录失败句（原生 fetchDirectory 的三支）：no_python 前缀 + 原句；其余用 act 侧的双语 message */
export function directoryErrorParts(dir: SlackDirectory, text: Text): { prefix: string; detail: string } {
  if (dir.error === "no_python") return { prefix: text("找不到可用的 python（", "No usable python ("), detail: `${dir.message ?? ""})` };
  if (dir.error === "directory_failed") return { prefix: "", detail: text("读取 Slack 目录失败——稍后重试", "Couldn't read the Slack directory — try again later") + (dir.message ? ` (${dir.message})` : "") };
  return { prefix: "", detail: dir.message || dir.error || text("读取 Slack 目录失败——稍后重试", "Couldn't read the Slack directory — try again later") };
}

function listEffective(effective: unknown): string[] {
  return Array.isArray(effective) ? effective.map((v) => String(v)) : [];
}

export function SlackDirectoryPicker() {
  const { text } = useI18n();
  const { settingsCatalog } = useAppState();
  const [dir, setDir] = useState<SlackDirectory | null>(null);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<{ prefix: string; detail: string } | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const section = settingsCatalog?.sections.find((s) => s.id === "slack");
  const channelIds = listEffective(section?.fields.find((f) => f.key === "slack_channels")?.effective);
  const people = listEffective(section?.fields.find((f) => f.key === "watch_people")?.effective);
  const hasData = dir !== null && (dir.channels.length > 0 || dir.users.length > 0);

  async function load() {
    setLoading(true);
    setFailure(null);
    try {
      const result = await fetchSlackDirectory(hasData);
      if (result.ok) {
        setDir(result);
      } else {
        setFailure(directoryErrorParts(result, text));
      }
    } catch (err) {
      setFailure({ prefix: "", detail: errorMessage(err) });
    } finally {
      setLoading(false);
    }
  }

  async function write(key: "slack_channels" | "watch_people", next: string[]) {
    setSaveError(null);
    try {
      await saveSettingsSection("slack", { [key]: next });
    } catch (err) {
      setSaveError(errorMessage(err));
    }
  }

  const toggleChannel = (entry: SlackDirEntry) =>
    void write("slack_channels", channelIds.includes(entry.id) ? channelIds.filter((id) => id !== entry.id) : [...channelIds, entry.id]);
  const togglePerson = (entry: SlackDirEntry) =>
    void write("watch_people", people.includes(entry.name) ? people.filter((h) => h !== entry.name) : [...people, entry.name]);

  return (
    <div className="slack-directory">
      <div className="settings-subhead-row">
        <div className="settings-subhead">{text("监控范围", "What to watch")}</div>
        <button type="button" className="btn" disabled={loading} onClick={() => void load()}>
          {loading ? text("加载中…", "Loading…") : hasData ? text("刷新", "Refresh") : text("加载频道和成员", "Load channels & members")}
        </button>
      </div>
      <p className="settings-helper">{text("DM 和群消息总是全看（有人私你 = 大概率要处理）。频道只看你勾选的这些、且 @你 才建卡；「关注的人」的第一位按你的 manager 处理（会议纪要识别用）。这里没改过时沿用 config.yaml 的配置。", "DMs and group DMs are always watched (a DM usually needs you). Channels: only the ones you check here, and only when you're @mentioned; the first \"watched person\" is treated as your manager (for meeting-note detection). Until you change something here, config.yaml stays in charge.")}</p>
      {failure && (
        <p className="settings-warning" role="alert">{failure.prefix ? <span>{failure.prefix}</span> : null}<span>{failure.detail}</span></p>
      )}
      {saveError && (
        <p className="settings-warning" role="alert"><span>{text("保存设置失败: ", "Failed to save settings: ")}</span><span>{saveError}</span></p>
      )}
      {dir && dir.channels.length > 0 && (
        <PickerList
          title={text("频道（@你 才建卡）", "Channels (card only when @mentioned)")}
          entries={dir.channels}
          isOn={(e) => channelIds.includes(e.id)}
          label={(e) => `#${e.name}`}
          onToggle={toggleChannel}
        />
      )}
      {dir && dir.users.length > 0 && (
        <PickerList
          title={text("关注的人（第一位 = 你的 manager）", "Watched people (first = your manager)")}
          entries={dir.users}
          isOn={(e) => people.includes(e.name)}
          label={(e) => (e.real_name ? `@${e.name}（${e.real_name}）` : `@${e.name}`)}
          onToggle={togglePerson}
        />
      )}
    </div>
  );
}

interface PickerListProps {
  title: string;
  entries: SlackDirEntry[];
  isOn: (entry: SlackDirEntry) => boolean;
  label: (entry: SlackDirEntry) => string;
  onToggle: (entry: SlackDirEntry) => void;
}

/** 原生 pickerList：标题 + 「筛选…」+ 勾选表（已勾的浮顶、最多 200 项、溢出一句） */
function PickerList({ title, entries, isOn, label, onToggle }: PickerListProps) {
  const { text } = useI18n();
  const [filter, setFilter] = useState("");
  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const filtered = entries.filter((e) => !q || e.name.toLowerCase().includes(q) || (e.real_name ?? "").toLowerCase().includes(q));
    return { filtered, rows: [...filtered.filter(isOn), ...filtered.filter((e) => !isOn(e))].slice(0, LIST_CAP) };
  }, [entries, filter, isOn]);
  const more = shown.filtered.length - shown.rows.length;
  return (
    <div className="slack-picker">
      <div className="settings-subhead-row">
        <span className="settings-knob-label">{title}</span>
        <input type="text" className="settings-input is-filter" placeholder={text("筛选…", "Filter…")} aria-label={text("筛选…", "Filter…")} value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>
      <ul className="slack-picker-list">
        {shown.rows.map((entry) => (
          <li key={entry.id}>
            <label className="settings-radio">
              <input type="checkbox" checked={isOn(entry)} onChange={() => onToggle(entry)} />
              {label(entry)}
            </label>
          </li>
        ))}
      </ul>
      {more > 0 && <p className="settings-helper">{text(`还有 ${more} 项——用上面的筛选框缩小范围`, `${more} more — narrow it down with the filter above`)}</p>}
    </div>
  );
}
