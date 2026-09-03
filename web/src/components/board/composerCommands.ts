// 捕获输入框的历史与斜杠命令（原生 Store.swift 捕获历史 ↑/↓ 20 条 + Composer.swift `/rec /open /lang`，
// s4 1.8）。历史 = localStorage `captureHistory`（键名逐字镜像原生 UserDefaults，§66.2 setting:prefs:*；
// 最近 20 条，去重、最新在前；旧键 `zai.captureHistory` 首次读到即搬过来）；斜杠命令不发 inbox：
//   /rec off|screen|screen_audio   → 壳桥 setRecording（无桥时如实说只在 app 里可用）
//   /lang zh|en                    → setLanguage
//   /open board|trash|archive|settings|permissions|diagnostics|setup → 整页导航
// 纯逻辑放这里便于 vitest；LaneComposer 只做接线。
import type { Language } from "../../i18n";
import { buildAppUrl, navigate, type AppPage } from "../../route";
import { callShell, hasShellBridge } from "../../shellBridge";
import { setLanguage } from "../../store";

export const HISTORY_KEY = "captureHistory";
/** v1.0 之前的键名；读到即搬到同名键并删掉（一次性迁移，不留第二份） */
export const LEGACY_HISTORY_KEY = "zai.captureHistory";
export const HISTORY_MAX = 20;

function parseHistory(raw: string | null): string[] {
  try {
    const parsed = JSON.parse(raw ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string").slice(0, HISTORY_MAX) : [];
  } catch {
    return [];
  }
}

export function readHistory(): string[] {
  try {
    const current = window.localStorage.getItem(HISTORY_KEY);
    if (current !== null) return parseHistory(current);
    const legacy = window.localStorage.getItem(LEGACY_HISTORY_KEY);
    if (legacy === null) return [];
    const migrated = parseHistory(legacy);
    window.localStorage.setItem(HISTORY_KEY, JSON.stringify(migrated));
    window.localStorage.removeItem(LEGACY_HISTORY_KEY);
    return migrated;
  } catch {
    return [];
  }
}

/** 最新在前、去重、封顶 20；写失败静默 */
export function pushHistory(entry: string): string[] {
  const next = [entry, ...readHistory().filter((x) => x !== entry)].slice(0, HISTORY_MAX);
  try {
    window.localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
  } catch {
    /* 隐私模式 */
  }
  return next;
}

const PAGES: readonly AppPage[] = ["board", "trash", "archive", "settings", "permissions", "diagnostics", "setup"];
const REC_MODES = ["off", "screen", "screen_audio"] as const;

export type CommandResult = { handled: false } | { handled: true; note: string };

type Text = (zh: string, en: string) => string;

/** 解析并执行一条斜杠命令；不是命令 → handled:false（调用方按普通捕获发出） */
export async function runSlashCommand(raw: string, text: Text): Promise<CommandResult> {
  const trimmed = raw.trim();
  if (!trimmed.startsWith("/")) return { handled: false };
  const [cmd, arg = ""] = trimmed.slice(1).split(/\s+/, 2);
  switch (cmd) {
    case "rec": {
      if (!(REC_MODES as readonly string[]).includes(arg)) return { handled: true, note: text("用法：/rec off|screen|screen_audio", "Usage: /rec off|screen|screen_audio") };
      if (!hasShellBridge()) return { handled: true, note: text("/rec 只在看板 app（壳）里可用", "/rec only works inside the board app") };
      try {
        await (arg === "off" ? callShell("setRecording", { on: false }) : callShell("setRecording", { on: true, mode: arg }));
        return { handled: true, note: text(`录制 → ${arg}`, `Recording → ${arg}`) };
      } catch (e) {
        return { handled: true, note: e instanceof Error ? e.message : String(e) };
      }
    }
    case "lang": {
      if (arg !== "zh" && arg !== "en") return { handled: true, note: text("用法：/lang zh|en", "Usage: /lang zh|en") };
      setLanguage(arg as Language);
      return { handled: true, note: text(`语言 → ${arg}`, `Language → ${arg}`) };
    }
    case "open": {
      if (!(PAGES as readonly string[]).includes(arg)) return { handled: true, note: text(`用法：/open ${PAGES.join("|")}`, `Usage: /open ${PAGES.join("|")}`) };
      navigate(buildAppUrl(window.location.href, arg as AppPage, null));
      return { handled: true, note: text(`打开 ${arg}…`, `Opening ${arg}…`) };
    }
    default:
      return { handled: true, note: text("未知命令；可用：/rec /lang /open", "Unknown command; available: /rec /lang /open") };
  }
}
