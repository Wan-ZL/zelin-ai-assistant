// 语言切换（G7 shell，自写非 fork）：按钮文案显示"目标语言"（当前 zh 显示 EN，反之显示 中）。
// 写路径走 store.setLanguage（持久化 zai.lang）；同时清掉 URL 上一次性的 ?lang= 覆写，
// 否则刷新后 query 又压过用户刚选的语言（store.detectInitialLanguage 的优先级）。
import { useI18n } from "../../i18n";
import { setLanguage } from "../../store";

export function LanguageToggle() {
  const { language, text } = useI18n();
  const next = language === "zh" ? "en" : "zh";

  const handleToggle = () => {
    setLanguage(next);
    try {
      const url = new URL(window.location.href);
      if (url.searchParams.has("lang")) {
        url.searchParams.delete("lang");
        window.history.replaceState(null, "", url);
      }
    } catch {
      /* URL 操作失败不影响语言切换本身 */
    }
  };

  return (
    <button
      type="button"
      className="shell-icon-button shell-lang-toggle"
      title={text("切换到英文", "Switch to Chinese")}
      aria-label={text("切换到英文", "Switch to Chinese")}
      onClick={handleToggle}
    >
      {language === "zh" ? "EN" : "中"}
    </button>
  );
}
