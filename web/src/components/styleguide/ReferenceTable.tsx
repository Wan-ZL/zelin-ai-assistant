// 第 1 节「老 app 参照区」：一行一个语义色，老值（字面 hex，历史数据）对新 token
// （活色块 var(--x)，随主题切换）。渲染样本列用真实 class（.btn/.chip 全家）——
// 这些 class 就是看板组件用的那套，token 一改这里立刻跟着变。数据源 palette.ts。
import { useI18n } from "../../i18n";
import { PALETTE_ROWS, type PaletteRow, type Sample } from "./palette";

/** 老值色块：字面 hex 是历史参照数据（palette.ts 头注释的例外声明），非 UI token */
function OldSwatches({ hexes }: { hexes: string[] }) {
  if (hexes.length === 0) return <span className="sg-swatch-none">—</span>;
  return (
    <span className="sg-swatch-row">
      {hexes.map((hex) => (
        <span key={hex} className="sg-swatch" style={{ background: hex }} title={hex} />
      ))}
    </span>
  );
}

/** 新值活色块：走 var(--token)，切主题时跟着变 */
function TokenSwatches({ vars }: { vars: string[] }) {
  if (vars.length === 0) return null;
  return (
    <span className="sg-swatch-row">
      {vars.map((name) => (
        <span key={name} className="sg-swatch" style={{ background: `var(${name})` }} title={name} />
      ))}
    </span>
  );
}

const LANE_DOT_TOKENS = ["--status-todo", "--status-progress", "--status-review", "--status-done", "--status-backlog"];
const BG_LAYER_TOKENS = ["--bg", "--sidebar-bg", "--surface", "--column-header"];

function SampleCell({ sample }: { sample: Sample }) {
  const { text } = useI18n();
  switch (sample.kind) {
    case "button":
      return <button type="button" className={sample.className}>{text(sample.zh, sample.en)}</button>;
    case "chip":
      return <span className={sample.className}>{text(sample.zh, sample.en)}</span>;
    case "chips":
      return (
        <span className="card-badges">
          {sample.labels.map((label) => (
            <span key={label} className={sample.className}>{label}</span>
          ))}
        </span>
      );
    case "dots":
      // 与 Lane.tsx 的 .lane-dot 同 class 同 token 用法
      return (
        <span className="sg-swatch-row">
          {LANE_DOT_TOKENS.map((name) => (
            <span key={name} className="lane-dot" style={{ background: `var(${name})` }} title={name} />
          ))}
        </span>
      );
    case "layers":
      return (
        <span className="sg-swatch-row">
          {BG_LAYER_TOKENS.map((name) => (
            <span key={name} className="sg-swatch sg-swatch-bordered" style={{ background: `var(${name})` }} title={name} />
          ))}
        </span>
      );
    case "text":
      return <span className="sg-meta-text">{text(sample.zh, sample.en)}</span>;
  }
}

function Row({ row }: { row: PaletteRow }) {
  const { text, language } = useI18n();
  return (
    <tr className={`sg-ref-row${row.flagged ? " is-flagged" : ""}`}>
      <td>
        {row.flagged && <span className="sg-flag" title={text("老 vs 新可见差异", "Visible old-vs-new difference")}>⚠️ </span>}
        {text(row.zh, row.en)}
        {row.noteZh && (
          <div className="sg-note">{language === "zh" ? row.noteZh : row.noteEn ?? row.noteZh}</div>
        )}
      </td>
      <td>
        <OldSwatches hexes={row.oldSwatches} />
        <div className="sg-value">{row.oldValue}</div>
      </td>
      <td><code>{row.token}</code></td>
      <td>
        <TokenSwatches vars={row.swatchVars} />
        <div className="sg-value">dark {row.newDark}</div>
        <div className="sg-value">light {row.newLight}</div>
      </td>
      <td><SampleCell sample={row.sample} /></td>
    </tr>
  );
}

export function ReferenceTable() {
  const { text } = useI18n();
  return (
    <table className="sg-ref-table">
      <thead>
        <tr>
          <th>{text("语义", "Semantic")}</th>
          <th>{text("老 app 色值", "Old app value")}</th>
          <th>{text("新 token 名", "New token")}</th>
          <th>{text("新色值", "New value")}</th>
          <th>{text("渲染样本", "Rendered sample")}</th>
        </tr>
      </thead>
      <tbody>
        {PALETTE_ROWS.map((row) => (
          <Row key={row.key} row={row} />
        ))}
      </tbody>
    </table>
  );
}
