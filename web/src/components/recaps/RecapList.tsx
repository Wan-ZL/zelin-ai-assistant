// 会议纪要页左列（CONTRACT §63 / issue #129 §3）：按日分组的行 = `12:56–13:16 · Zoom · 20 min` + badge。
// 纯受控：选中态由 RecapsPage 持有；不发请求。生成态（§63.8 生成中 / 生成未落地）由页面算好按 key 传入。
import { useI18n } from "../../i18n";
import type { RecapRow } from "../../types";
import { badgesFor, groupByDay, rowLabel, type GenerationPhase } from "./recapText";

export interface RecapListProps {
  rows: RecapRow[];
  selectedKey: string | null;
  onSelect: (key: string) => void;
  phases?: Record<string, GenerationPhase>;
}

export function RecapList({ rows, selectedKey, onSelect, phases = {} }: RecapListProps) {
  const { language } = useI18n();
  const groups = groupByDay(rows);
  return (
    <nav className="recap-list" aria-label={language === "zh" ? "会议列表" : "Meetings"}>
      {groups.map((group) => (
        <section key={group.day} className="recap-day">
          <h3 className="recap-day-title">{group.day}</h3>
          <ul className="recap-day-rows">
            {group.rows.map((row) => {
              const isSelected = row.key === selectedKey;
              return (
                <li key={row.key}>
                  <button
                    type="button"
                    className={`recap-row${isSelected ? " is-selected" : ""}`}
                    aria-current={isSelected ? "true" : undefined}
                    onClick={() => onSelect(row.key)}
                  >
                    <span className="recap-row-label">{rowLabel(row)}</span>
                    <span className="recap-row-badges">
                      {badgesFor(row, phases[row.key] ?? "idle").map((badge) => (
                        <span key={badge.id} className={`chip chip-${badge.tone === "quiet" ? "outline" : badge.tone}`}>
                          {language === "zh" ? badge.zh : badge.en}
                        </span>
                      ))}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </nav>
  );
}
