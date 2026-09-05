// 捕获输入框的历史与斜杠命令（原生 Store.swift 捕获历史 ↑/↓ 20 条 + SlashCommands `/rec /open /lang`，
// s4 1.8；§41 2026-09-05 追记）。历史 = localStorage `captureHistory`（键名逐字镜像原生 UserDefaults，
// §66.2 setting:prefs:*；最近 20 条，去重、最新在前；旧键 `zai.captureHistory` 首次读到即搬过来）。
// 斜杠命令**只有三个动词**（原生 SlashCommands.isCommand `^/(rec|open|lang)\b`）：以 "/" 开头的其他任何
// 文字——尤其是绝对路径「/Users/… 整理一下」——都是普通捕获，照常铸卡；命令不发 inbox：
//   /rec off|screen|audio|screen_audio → 壳桥 setRecording（audio = 原生词，映到壳的 screen_audio；无桥时如实说只在 app 里可用）
//   /lang zh|en                        → setLanguage
//   /open board|deps|ingest|settings|about|trash|archive|permissions|diagnostics|setup → 整页导航
//     （原生五页在前、web 独有页在后；deps / diagnostics 自 D30 起都落设置页的依赖检查区）
// 动词与参数都不分大小写（原生 `parts[1].lowercased()`）。纯逻辑放这里便于 vitest；LaneComposer 只做接线。
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

/** `/open` 词表：原生 MainSection 五页（Store.swift `sections`，顺序同原生 hintLine）在前，web 独有页在后——
 *  是原生词表的超集（§54.1）。用法句与 "/" 提示行都从这里派生，没有第二份词表。 */
const PAGES: readonly AppPage[] = ["board", "deps", "ingest", "settings", "about", "trash", "archive", "permissions", "diagnostics", "setup"];
/** 壳桥 setRecording 认的 mode（§61.1） */
const REC_MODES = ["off", "screen", "screen_audio"] as const;
type RecMode = (typeof REC_MODES)[number];
/** 用户词 → 壳 mode（原生 Store.swift `modes` 表：`audio` = screen_audio）；Map 而非对象，免得 `constructor` 之类原型键蒙混 */
const REC_ALIASES = new Map<string, RecMode>([["audio", "screen_audio"]]);
/** 用法句 / 提示行里 /rec 的词表：原生三词 + web 既有的 screen_audio */
const REC_WORDS = ["off", "screen", "audio", "screen_audio"] as const;

/** 只有这三个动词算命令（原生 SlashCommands.isCommand `^/(rec|open|lang)\b`）。用 `(?=\s|$)` 而不是 `\b`：
 *  JS 的 `\b` 只认 ASCII 词字符，「/rec整理」会被 `\b` 判成命令，而原生 ICU 的 `\b` 是 Unicode 词界——它是普通捕获。
 *  动词不分大小写（原生的参数已 lowercased；动词放宽是 web 的超集，textarea 首字母自动大写不至于吞掉命令）。 */
const COMMAND_RE = /^\/(rec|open|lang)(?=\s|$)/i;

/** handled:false = 不是命令（按普通捕获发出）；note = 成功回执；error = 原生 Composer.swift 的失败形——
 *  `unrecognized`（三个动词之一但参数打错：「未识别或参数错误：」+ 原文，输入保留）或 `io`（命令本身坏了：原句）。 */
export type CommandResult =
  | { handled: false }
  | { handled: true; note: string }
  | { handled: true; error: { kind: "unrecognized"; input: string; usage: string } | { kind: "io"; message: string } };

type Text = (zh: string, en: string) => string;

/** 打「/…」草稿时输入框下的一行提示（原生 Store.swift SlashCommands.hintLine
 *  「命令：/rec off|screen|audio · /open board|deps|ingest|settings|about · /lang zh|en」）——词表与用法句同源，
 *  所以列的是 web 的完整词表（原生词在前）。 */
export function hintLine(text: Text): string {
  const rec = REC_WORDS.join("|");
  const pages = PAGES.join("|");
  return text(`命令：/rec ${rec} · /open ${pages} · /lang zh|en`, `Commands: /rec ${rec} · /open ${pages} · /lang zh|en`);
}

/** 解析并执行一条斜杠命令；不是命令（含 "/" 开头的路径等一切非三动词文字）→ handled:false（调用方按普通捕获发出） */
export async function runSlashCommand(raw: string, text: Text): Promise<CommandResult> {
  const trimmed = raw.trim();
  const match = COMMAND_RE.exec(trimmed);
  if (!match) return { handled: false };
  const verb = match[1].toLowerCase() as "rec" | "open" | "lang";
  // 第二个 token 是参数，之后的忽略（原生 parts[1]）；参数不分大小写（原生 `.lowercased()`）
  const arg = (trimmed.slice(match[0].length).trim().split(/\s+/, 1)[0] ?? "").toLowerCase();
  // 参数打错 = 原生 SlashCommands.run 返回 false 的那条路：输入保留，一行「未识别或参数错误：」+ 用法
  const unrecognized = (usage: string): CommandResult => ({ handled: true, error: { kind: "unrecognized", input: trimmed, usage } });
  switch (verb) {
    case "rec": {
      const mode = REC_ALIASES.get(arg) ?? ((REC_MODES as readonly string[]).includes(arg) ? (arg as RecMode) : null);
      if (mode === null) return unrecognized(text(`用法：/rec ${REC_WORDS.join("|")}`, `Usage: /rec ${REC_WORDS.join("|")}`));
      if (!hasShellBridge()) return { handled: true, note: text("/rec 只在看板 app（壳）里可用", "/rec only works inside the board app") };
      try {
        await (mode === "off" ? callShell("setRecording", { on: false }) : callShell("setRecording", { on: true, mode }));
        return { handled: true, note: text(`录制 → ${mode}`, `Recording → ${mode}`) };
      } catch (e) {
        return { handled: true, error: { kind: "io", message: e instanceof Error ? e.message : String(e) } };
      }
    }
    case "lang": {
      if (arg !== "zh" && arg !== "en") return unrecognized(text("用法：/lang zh|en", "Usage: /lang zh|en"));
      setLanguage(arg as Language);
      return { handled: true, note: text(`语言 → ${arg}`, `Language → ${arg}`) };
    }
    case "open": {
      if (!(PAGES as readonly string[]).includes(arg)) return unrecognized(text(`用法：/open ${PAGES.join("|")}`, `Usage: /open ${PAGES.join("|")}`));
      navigate(buildAppUrl(window.location.href, arg as AppPage, null));
      return { handled: true, note: text(`打开 ${arg}…`, `Opening ${arg}…`) };
    }
  }
}
