// 设置页（CONTRACT §59；?page=settings 深链，顶栏齿轮入口）。section：「模型」（D22，§59）、「素材库」（D11，§62）、「会议纪要」（§63）、「Skills」（D13，§67）。
// 页面级只做骨架：返回链接 + 标题 + section 列表；每个 section 自己拉自己的数据（经 store action）。
// 后续 P4 补齐的设置区（Gmail/Slack/录制/telemetry…）逐个加 section，别在这里堆 useState。
import "../components/chrome/chrome.css";
import "../components/settings/settings.css";
import { MaterialsSection } from "../components/settings/MaterialsSection";
import { ModelsSection } from "../components/settings/ModelsSection";
import { RecapSection } from "../components/settings/RecapSection";
import { SkillsSection } from "../components/settings/SkillsSection";
import { useI18n } from "../i18n";
import { buildAppUrl } from "../route";

export function SettingsPage() {
  const { text } = useI18n();
  return (
    <main className="settings-page">
      <a className="trash-back-link" href={buildAppUrl(window.location.href, "board", null).toString()}>
        {text("← 返回看板", "← Back to board")}
      </a>
      <div className="settings-page-head">
        <h2 className="settings-page-title">{text("设置", "Settings")}</h2>
      </div>
      <ModelsSection />
      <MaterialsSection />
      {/* §63 会议纪要：会后自动出稿 / 默认语言 / Slack 草稿开关（默认关） */}
      <RecapSection />
      <SkillsSection />
    </main>
  );
}
