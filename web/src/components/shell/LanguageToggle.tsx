// 语言切换（G7 shell，自写非 fork）：按钮文案显示"目标语言"（当前 zh 显示 EN，反之显示 中）。
// 写路径走 store.chooseLanguage（D37，§15 追记：UI 立刻切 + PUT general.language——与 `/lang`、向导、设置区「保存」同一把开关，
// python 侧通知 / 修法句与壳都读那个键；localStorage zai.lang 只是首帧缓存）。URL 上一次性的 ?lang= 覆写也由它顺手摘掉
// （此前只有这个按钮摘、`/lang` 与向导单选不摘——刷新后 query 又压过刚选的语言、且有 ?lang= 时不水合），三个入口一个样。
import { useI18n } from "../../i18n";
import { chooseLanguage } from "../../store";

export function LanguageToggle() {
  const { language, text } = useI18n();
  const next = language === "zh" ? "en" : "zh";

  const handleToggle = () => {
    void chooseLanguage(next);
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
