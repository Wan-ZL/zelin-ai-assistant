// 第 5 节 Typography & spacing：字号/字重梯逐行渲染自 styles/typeScale.ts（原生看板角色 →
// Swift 源行 → tokens.css --type-* token），每行用 `font: var(token)` 真渲染，token 一改本页即变；
// 另有文字四层 token、间距与圆角梯、阴影 token。
// 双主题注：本页与看板同一棵 token 树——顶栏 ThemeToggle（真组件）切 data-theme，
// 全部活色块/样本立即跟随；light / dark 各看一遍即完成核对。
import { useI18n } from "../../i18n";
import { TYPE_SCALE, WEIGHT_OF } from "../../styles/typeScale";
import { SpecimenNote } from "./SpecimenNote";

const TEXT_TOKENS = ["--text-primary", "--text-secondary", "--text-tertiary", "--text-quaternary"];
const SPACING = [4, 6, 8, 12, 16, 20];
const RADII: Array<[string, string]> = [["5px", ".btn"], ["6px", ".task-card / .chrome"], ["10px", ".zai-dialog"], ["999px", ".chip 胶囊"]];

export function TypographySection() {
  const { text } = useI18n();
  return (
    <div className="sg-grid">
      <figure className="sg-specimen sg-specimen-wide">
        <table className="sg-ref-table sg-type-table">
          <thead>
            <tr>
              <th>{text("样本（font: var(token) 真渲染）", "Sample (rendered with font: var(token))")}</th>
              <th>{text("原生角色", "Native role")}</th>
              <th>{text("Swift 源行", "Swift source")}</th>
              <th>token</th>
              <th>{text("值", "Value")}</th>
            </tr>
          </thead>
          <tbody>
            {TYPE_SCALE.map((row) => (
              <tr key={row.token} className="sg-type-row">
                <td>
                  <span className="sg-type-sample" style={{ font: `var(${row.token})` }}>
                    {text("看板 Board 文字 Aa 0123", "Board 看板 text Aa 0123")}
                  </span>
                </td>
                <td>{text(row.zh, row.en)}</td>
                <td>
                  <code>
                    {row.swift.file}:{row.swift.line}
                  </code>
                  <div className="sg-note">
                    {row.swift.size}pt · {row.swift.weight} ({WEIGHT_OF[row.swift.weight]}){row.swift.mono ? " · monospaced" : ""}
                  </div>
                </td>
                <td><code>{row.token}</code></td>
                <td><code>{row.font}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
        <SpecimenNote
          zh="字号/字重梯逐字镜像原生看板（mac/Sources，D3 冻结规格；1pt = 1px，SF regular/medium/semibold/bold = 400/500/600/700）。truth = tokens.css 的 type-scale 块；本表来自 styles/typeScale.ts；typeScale.test.ts 钉 CSS ↔ 表，tests/test_web_type_scale_mirror.py 钉 表 ↔ Swift 源行。字体 = tokens.css --font-sans 系统栈（-apple-system → PingFang SC …）。"
          en="Type scale mirrors the native board verbatim (mac/Sources, the frozen D3 spec; 1pt = 1px, SF regular/medium/semibold/bold = 400/500/600/700). Truth = the type-scale block in tokens.css; this table comes from styles/typeScale.ts; typeScale.test.ts pins CSS ↔ table and tests/test_web_type_scale_mirror.py pins table ↔ Swift source line. Font = the tokens.css --font-sans system stack (-apple-system → PingFang SC …)."
        />
      </figure>
      <figure className="sg-specimen">
        {TEXT_TOKENS.map((token) => (
          <p key={token} className="sg-type-row" style={{ color: `var(${token})` }}>
            <code>{token}</code> — {text("文字层级示例", "text layer sample")}
          </p>
        ))}
        <SpecimenNote
          zh="文字四层 ← 原生颜色映射：.primary → primary / .secondary → secondary（列头、meta、复制行、展开详情 都是它）/ .secondary.opacity(0.85) → tertiary（卡 id、引文）/ .secondary.opacity(0.7/0.55) → quaternary（空列）。色值本身不动（owner 验收过的白底对比度阶梯）。"
          en="Four text layers ← native color mapping: .primary → primary / .secondary → secondary (lane heads, meta, copy line, details all use it) / .secondary.opacity(0.85) → tertiary (card id, quotes) / .secondary.opacity(0.7/0.55) → quaternary (empty lanes). The hex values themselves are unchanged (owner-approved contrast ladder)."
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
