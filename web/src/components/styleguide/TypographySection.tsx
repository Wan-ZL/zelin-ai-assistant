// 第 5 节 Typography & spacing：现役字号/字重梯（值抄自 board.css / shell.css，
// 系统字体栈见 tokens.css :root）、文字四层 token、间距与圆角梯、阴影 token。
// 双主题注：本页与看板同一棵 token 树——顶栏 ThemeToggle（真组件）切 data-theme，
// 全部活色块/样本立即跟随；light / dark 各看一遍即完成核对。
import { useI18n } from "../../i18n";
import { SpecimenNote } from "./SpecimenNote";

const TYPE_SCALE: Array<{ px: number; weight: number; zh: string; en: string }> = [
  { px: 15, weight: 600, zh: "详情摘要（Mac 卡面 15pt 语义的 web 对应，DetailDrawer）", en: "Detail summary (DetailDrawer)" },
  { px: 14, weight: 600, zh: "顶栏标题 .shell-title / 弹窗标题 .zai-dialog h2", en: "Shell title / dialog heading" },
  { px: 13, weight: 600, zh: "列头 .column-header", en: "Lane header .column-header" },
  { px: 12, weight: 600, zh: "卡标题 .card-title", en: "Card title .card-title" },
  { px: 12, weight: 400, zh: "卡摘要 .card-summary / 弹窗正文 / 输入框", en: "Card summary / dialog body / inputs" },
  { px: 11, weight: 400, zh: "说明行 .card-line / 列帮助 .column-help / 按钮 .btn", en: "Card line / lane help / buttons" },
  { px: 10, weight: 400, zh: "卡号 .card-id（tabular-nums）/ chip 文字", en: "Card id (tabular-nums) / chip text" },
];

const TEXT_TOKENS = ["--text-primary", "--text-secondary", "--text-tertiary", "--text-quaternary"];
const SPACING = [4, 6, 8, 12, 16, 20];
const RADII: Array<[string, string]> = [["5px", ".btn"], ["6px", ".task-card / .chrome"], ["10px", ".zai-dialog"], ["999px", ".chip 胶囊"]];

export function TypographySection() {
  const { text } = useI18n();
  return (
    <div className="sg-grid">
      <figure className="sg-specimen">
        {TYPE_SCALE.map((row) => (
          <p key={`${row.px}-${row.weight}-${row.zh}`} className="sg-type-row" style={{ fontSize: row.px, fontWeight: row.weight }}>
            {row.px}px / {row.weight} — {text(row.zh, row.en)}
          </p>
        ))}
        <SpecimenNote
          zh="字号梯（board.css / shell.css 现役值）；字体 = tokens.css :root 系统栈（-apple-system → PingFang SC …）"
          en="Type scale (live values from board.css / shell.css); font = the tokens.css :root system stack (-apple-system → PingFang SC …)"
        />
      </figure>
      <figure className="sg-specimen">
        {TEXT_TOKENS.map((token) => (
          <p key={token} className="sg-type-row" style={{ color: `var(${token})` }}>
            <code>{token}</code> — {text("文字层级示例", "text layer sample")}
          </p>
        ))}
        <SpecimenNote
          zh="文字四层（对应 Mac .primary/.secondary 透明度层）：primary 正文 / secondary 说明 / tertiary 弱化 / quaternary 卡号与空态"
          en="Four text layers (Mac's .primary/.secondary opacity ladder): primary body / secondary notes / tertiary muted / quaternary card ids & empty states"
        />
      </figure>
      <figure className="sg-specimen">
        <div className="sg-spacing-row">
          {SPACING.map((px) => (
            <span key={px} className="sg-spacing-item">
              <span className="sg-spacing-bar" style={{ width: px }} />
              <span className="sg-value">{px}</span>
            </span>
          ))}
        </div>
        <SpecimenNote
          zh="间距梯（px）：4 chip 间隙 · 6 卡内纵距/按钮距 · 8 列表卡距 · 12 列间距/板内边 · 16 顶栏横距 · 20 板左右边距"
          en="Spacing scale (px): 4 chip gap · 6 in-card gap/buttons · 8 card list gap · 12 lane gap/board padding · 16 header gutters · 20 board side padding"
        />
      </figure>
      <figure className="sg-specimen">
        <div className="sg-spacing-row">
          {RADII.map(([radius, usage]) => (
            <span key={radius} className="sg-radius-chip" style={{ borderRadius: radius }}>
              {radius} <span className="sg-value">{usage}</span>
            </span>
          ))}
        </div>
        <div className="sg-spacing-row">
          <span className="sg-shadow-chip" style={{ boxShadow: "var(--card-shadow)" }}>--card-shadow</span>
          <span className="sg-shadow-chip" style={{ boxShadow: "var(--dialog-shadow)" }}>--dialog-shadow</span>
        </div>
        <SpecimenNote
          zh="圆角梯与阴影 token（--card-shadow 卡面 / --dialog-shadow 弹窗，两主题各有定义）"
          en="Radius scale and shadow tokens (--card-shadow for cards / --dialog-shadow for dialogs, each theme defines its own)"
        />
      </figure>
      <figure className="sg-specimen">
        <p className="sg-meta-text">
          {text(
            "双主题核对：用右上角主题开关（真 ThemeToggle，写 data-theme + localStorage zai.theme）切换——本页全部样本走 var(--token)，dark 值 = Mac 现役外观继承，light 值 = 同色相按白底对比度加深（vnext.md §10）。未显式选择时跟随系统 prefers-color-scheme。",
            "Both-theme check: use the header ThemeToggle (the real one; writes data-theme + localStorage zai.theme). Every sample here reads var(--token) — dark values inherit the live Mac look, light values darken the same hues for white-background contrast (vnext.md §10). With no explicit choice the page follows prefers-color-scheme.",
          )}
        </p>
      </figure>
    </div>
  );
}
