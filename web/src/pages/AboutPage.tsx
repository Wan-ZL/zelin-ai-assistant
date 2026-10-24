// 关于页（CONTRACT §26 / §68.6 / §79 / §54.4；?page=about，左侧导航栏最后一项）：原生 Pages.swift AboutView 的
// web 落点——页面骨架 + AboutSection（应用 / 版本 / 更新 / 仓库 / 数据目录 / 卸载 + 壳在场时的登录时启动）。
// 设置页不再重复这一区（原生设置里也没有「关于」——它是 sidebar 页）。
import "../components/settings/settings.css";
import { AboutSection } from "../components/settings/AboutSection";
import { useI18n } from "../i18n";

export function AboutPage() {
  const { text } = useI18n();
  return (
    <main className="settings-page">
      <div className="settings-page-head">
        <h2 className="settings-page-title">{text("关于", "About")}</h2>
      </div>
      <AboutSection />
    </main>
  );
}
