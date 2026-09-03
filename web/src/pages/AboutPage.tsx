// 关于页（CONTRACT §26 / §68.6 / §54.4；?page=about，左侧导航栏最后一项）：原生 Pages.swift AboutView 的
// web 落点——页面骨架 + AboutSection（应用 / 版本 / 更新 / 仓库 / 用量报告 / 卸载 + 壳在场时的登录时启动）。
// 设置页不再重复这一区（原生设置里也没有「关于」——它是 sidebar 页）。
import "../components/chrome/chrome.css";
import "../components/settings/settings.css";
import { AboutSection } from "../components/settings/AboutSection";
import { useI18n } from "../i18n";
import { buildAppUrl } from "../route";

export function AboutPage() {
  const { text } = useI18n();
  return (
    <main className="settings-page">
      <a className="trash-back-link" href={buildAppUrl(window.location.href, "board", null).toString()}>{text("← 返回看板", "← Back to board")}</a>
      <div className="settings-page-head">
        <h2 className="settings-page-title">{text("关于", "About")}</h2>
      </div>
      <AboutSection />
    </main>
  );
}
