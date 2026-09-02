// 活体样式指南（?page=styleguide，深链同 TrashPage 约定）。结构性保证：本页渲染的
// 是【真组件 + 真 token】——ProposalCard/RunningCard/ReviewCard/DoneCard/DebtCardItem/
// LaneComposer 原件挂载，chip/btn 用 board.css 现役 class；token 一改本页即变。
// 入口纪律：只有 URL 直达（看板头部保持干净；现无 About/footer 可挂小链接）。
// 五节：1 老 app 参照表（palette.ts，⚠️=可见差异）2 Buttons 3 Chips 4 Cards 5 Type & spacing。
import type { ReactNode } from "react";
import "../components/styleguide/styleguide.css";
import { useI18n } from "../i18n";
import { buildAppUrl } from "../route";
import { ButtonsSection } from "../components/styleguide/ButtonsSection";
import { CardsSection } from "../components/styleguide/CardsSection";
import { ChipsSection } from "../components/styleguide/ChipsSection";
import { ReferenceTable } from "../components/styleguide/ReferenceTable";
import { TypographySection } from "../components/styleguide/TypographySection";

interface SectionProps {
  title: string;
  note: string;
  children: ReactNode;
}

function Section({ title, note, children }: SectionProps) {
  return (
    <section className="sg-section">
      <h3>{title}</h3>
      <p className="sg-section-note">{note}</p>
      {children}
    </section>
  );
}

export function StyleguidePage() {
  const { text } = useI18n();
  return (
    <main className="sg-page">
      <a className="sg-back-link" href={buildAppUrl(window.location.href, "board", null).toString()}>
        {text("← 返回看板", "← Back to board")}
      </a>
      <h2>{text("活体样式指南", "Living styleguide")}</h2>
      <p className="sg-intro">
        {text(
          "本页挂载的是看板的真组件与真 token（fixture 数据、SG- 前缀假 id）。卡片动词按钮是活的——点击会真发动作请求，server 会因卡不存在而拒绝，无副作用；例外：第 2 节的列顶输入框是真捕获通道，提交会真的铸卡。用右上角主题开关分别核对 dark / light；深链：?page=styleguide（仅 URL 直达，看板头部不放入口）。",
          "This page mounts the board's real components and real tokens (fixture data, fake SG- ids). Card verb buttons are live — clicks post real actions which the server rejects (unknown card), so they are side-effect free; exception: the lane composers in section 2 are the real capture channel and submitting there mints a real card. Use the header theme toggle to inspect dark and light; deep link: ?page=styleguide (URL-only, the board header stays clean).",
        )}
      </p>

      <Section
        title={text("1 · 老 app 参照区", "1 · Old-app reference")}
        note={text(
          "语义 | 老 app 色值（docs/design/vnext.md §10 提取表）| 新 token 名 | 新色值 | 渲染样本。⚠️ = 老 vs 新可见差异（差异依据同 §10 语义核对注记）。老值色块是字面 hex（历史数据），新值色块走 var(--token) 随主题变。",
          "Semantic | old-app value (extraction map, docs/design/vnext.md §10) | new token | new value | rendered sample. ⚠️ = visible old-vs-new difference (per the §10 semantics notes). Old swatches are literal hex (historical data); new swatches read var(--token) and follow the theme.",
        )}
      >
        <ReferenceTable />
      </Section>

      <Section
        title={text("2 · Buttons（真组件）", "2 · Buttons (real components)")}
        note={text(
          "全部动词按钮从真卡组件长出：批准/拒绝/修改/暂缓 · 评论/停止 · 验收/打回/复制成稿 · 退回待验收/永久完成 · 研究并提议/删除 · 捕获/直跑；末格为三变体 class + disabled 态与 hover token 注。",
          "Every verb button grows out of the real card components: approve/reject/comment/later · answer/comment/stop · accept/send-back/copy-draft · back-to-review/done-for-good · raise/delete · capture/run; the last cell shows the three variant classes with disabled states and hover-token notes.",
        )}
      >
        <ButtonsSection />
      </Section>

      <Section
        title={text("3 · Chips / labels", "3 · Chips / labels")}
        note={text(
          "chip 是 board.css class 约定（.chip / -accent / -warning / -danger / -success / -purple / -notice + 底色档 -quiet / 描边档 -outline；抽屉侧 zai-chip--improves/--merged），文案与词表和宿主组件同源；origin_trust 与 effective-tier 升档已立法未接线，按规划形态标 ⚠️ 展示。",
          "Chips are board.css class conventions (.chip / -accent / -warning / -danger / -success / -purple / -notice plus the -quiet tint and -outline steps; drawer-side zai-chip--improves/--merged) sharing labels and tables with their host components; origin_trust and effective-tier escalation are ratified but not wired yet, shown as ⚠️ planned shapes.",
        )}
      >
        <ChipsSection />
      </Section>

      <Section
        title={text("4 · Cards（每个 lane 状态一张真卡）", "4 · Cards (one real card per lane state)")}
        note={text(
          "proposal T1 / T2 / processing 占位 / queued / working / needs-input / review / done + 潜在任务；.task-card 基座 + .is-queued / .is-blocked 子状态。",
          "Proposal T1 / T2 / processing placeholder / queued / working / needs-input / review / done, plus backlog; .task-card base with .is-queued / .is-blocked substates.",
        )}
      >
        <CardsSection />
      </Section>

      <Section
        title={text("5 · Typography & spacing", "5 · Typography & spacing")}
        note={text(
          "字号/字重梯、文字四层 token、间距与圆角梯、阴影 token；本页随真 ThemeToggle 切换，dark / light 各核对一遍。",
          "Type scale, the four text-layer tokens, spacing and radius scales, shadow tokens; the page follows the real ThemeToggle — inspect both dark and light.",
        )}
      >
        <TypographySection />
      </Section>
    </main>
  );
}
