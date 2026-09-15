// 技能页（CONTRACT §67.5 / §54.4 2026-09-15 追记；?page=skills，左侧导航栏第四项——owner 决策 D78）：
// Claude Code skill 商店自己的一页。内容就是原设置页那一区的**同一个组件** SkillsSection（不复制：一行一个 skill、
// 启用 / 停用、在 Finder 显示、刷新、状态徽章、链接位置说明都在它里面，快照仍是 store.skills ← GET /api/skills），
// 本页只添页壳：「← 返回看板」+ 页头「技能 / Skills」+ 计数（读同一份快照，不多拉一次）。
// 页壳沿用回收站 / 永久性完成页的节拍（trash-page*，chrome.css）；区卡样式来自 settings.css（本页不在 settings-fold 里，
// 所以区自己的 <h3> 这回是可见的副标题，不像设置页里被 fold 区头顶掉）。
// web 自有页（原生 legacy app 没有这一页）：不进 ui/parity/native-inventory.json 的 rail / screen 清单；原生
// `screen:settings.skills` 的控件仍判在设置面上——parity.test.tsx 把本页与设置页渲进同一个池（§66.2）。
import "../components/chrome/chrome.css";
import "../components/settings/settings.css";
import { SkillsSection } from "../components/settings/SkillsSection";
import { useI18n } from "../i18n";
import { buildAppUrl } from "../route";
import { useAppState } from "../store";

export function SkillsPage() {
  const { text } = useI18n();
  const { skills } = useAppState();

  return (
    <main className="trash-page skills-page">
      <a className="trash-back-link" href={buildAppUrl(window.location.href, "board", null).toString()}>
        {text("← 返回看板", "← Back to board")}
      </a>
      <div className="trash-page-head">
        <h2 className="trash-page-title">{text("技能", "Skills")}</h2>
        {skills && <span className="trash-page-count">{skills.skills.length}</span>}
      </div>
      <SkillsSection />
    </main>
  );
}
