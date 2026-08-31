// 主题切换（G7 shell，自写非 fork）：per CONVENTIONS——写 localStorage["zai.theme"]
// + document.documentElement.dataset.theme（tokens.css 的 [data-theme] 覆写生效）。
// 初值 = 当前生效主题（dataset 显式值 > 系统偏好 matchMedia > light）；
// useState 只存这个纯本地瞬态镜像，全局视觉真源始终是 dataset + CSS。
import { useState } from "react";
import { useI18n } from "../../i18n";

type Theme = "light" | "dark";

function effectiveTheme(): Theme {
  const explicit = document.documentElement.dataset.theme;
  if (explicit === "light" || explicit === "dark") return explicit;
  try {
    // jsdom 无 matchMedia：guard 后测试环境回落 light
    if (typeof window.matchMedia === "function"
      && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      return "dark";
    }
  } catch {
    /* 环境不支持系统偏好查询：按 light 处理 */
  }
  return "light";
}

export function ThemeToggle() {
  const { text } = useI18n();
  const [theme, setTheme] = useState<Theme>(effectiveTheme);

  const handleToggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      window.localStorage.setItem("zai.theme", next);
    } catch {
      /* localStorage 不可写：本次会话仍生效，仅不持久化 */
    }
    setTheme(next);
  };

  const isDark = theme === "dark";
  return (
    <button
      type="button"
      className="shell-icon-button"
      aria-pressed={isDark}
      title={text("切换深浅色", "Toggle dark mode")}
      aria-label={text("切换深浅色", "Toggle dark mode")}
      onClick={handleToggle}
    >
      {isDark ? (
        /* 月亮：当前为暗色 */
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path
            d="M20.5 14.5A8.5 8.5 0 0 1 9.5 3.5a8.5 8.5 0 1 0 11 11Z"
            fill="currentColor"
          />
        </svg>
      ) : (
        /* 太阳：当前为浅色 */
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="4.2" fill="currentColor" />
          <g stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <path d="M12 2.5v2.4M12 19.1v2.4M2.5 12h2.4M19.1 12h2.4M5.2 5.2l1.7 1.7M17.1 17.1l1.7 1.7M18.8 5.2l-1.7 1.7M6.9 17.1l-1.7 1.7" />
          </g>
        </svg>
      )}
    </button>
  );
}
