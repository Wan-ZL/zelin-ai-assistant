// 开发者 · 开发会话 区的动作行（§68.1 / §68.7 追记；原生 SettingsMaintainer.openSession + launchRow）：「在终端打开开发会话」→
// POST /api/maintainer/terminal（server 读 effective 的仓库路径 / 会话 id 拼命令，客户端零参数）。
// 文案逐字原生（SettingsMaintainer.swift:215-226, 330-331）：帮助句「会在 <终端> 中打开（终端应用在「通用」里换）。」——终端名是
// server 算的（maintainer 区投影 add-only `terminal_app_name`：auto 要看装没装 Ghostty），**只读目录**：「通用 · 终端应用」一保存
// store 就重拉整本目录（store.saveSettingsSection），这句随之换名；回执里的同名键是同一个答案，页面不另存一份（存了就会盖过
// 目录的新值）；忙态「正在打开终端…」；
// 成功「已在终端打开 ✓ 直接告诉它要修什么、改什么就行。」（不再把命令原文当成功句）；open 失败（500，details.command）→
// 「打开终端失败——去「通用」检查终端应用设置，或手动在终端运行：」+ 可复制的命令；400 路径不存在 → 「路径不存在」；
// 400 会话 id 不合形状（details.check = session_id [+ reason]，启动前重检 config.yaml 里的 id）→ 目录 `check` 里的那句（server-owned，
// 按 UI 语言取键，FieldControl.checkSentence）；其它错误原文。
import { useState } from "react";
import { ApiError, postMaintainerTerminal } from "../../api";
import { useI18n } from "../../i18n";
import { useAppState } from "../../store";
import { CopyLine } from "../chrome/CopyLine";
import { checkSentence } from "./FieldControl";
import { errorMessage } from "./useToast";

type Note =
  | { kind: "busy" }
  | { kind: "opened" }
  | { kind: "open_failed"; command: string }
  | { kind: "error"; message: string };

const SESSION_ID_KEY = "maintainer_session_id";

/** ApiError 的 details 当 dict 读（缺 / 非对象 → {}） */
function detailsOf(err: unknown): Record<string, unknown> {
  return err instanceof ApiError && err.details && typeof err.details === "object" ? (err.details as Record<string, unknown>) : {};
}

export function MaintainerExtras() {
  const { text, language } = useI18n();
  const { settingsCatalog } = useAppState();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note | null>(null);

  const section = settingsCatalog?.sections.find((s) => s.id === "maintainer");
  const sessionField = section?.fields.find((f) => f.key === SESSION_ID_KEY);
  // 终端名只读目录投影（「通用」换了终端 → store 重拉目录 → 这里跟着变）；老 server 缺键 → 泛称「终端」
  const catalogName = typeof section?.terminal_app_name === "string" && section.terminal_app_name ? section.terminal_app_name : null;
  const terminalName = catalogName ?? text("终端", "the terminal");

  async function open() {
    setBusy(true);
    setNote({ kind: "busy" });
    try {
      await postMaintainerTerminal();
      setNote({ kind: "opened" });
    } catch (err) {
      setNote(failureNote(err));
    } finally {
      setBusy(false);
    }
  }

  function failureNote(err: unknown): Note {
    const details = detailsOf(err);
    if (err instanceof ApiError && err.status === 400) {
      if (/path does not exist/.test(err.message)) return { kind: "error", message: text("路径不存在", "Path doesn't exist") };
      const sentence = checkSentence(sessionField, details, language);
      if (sentence) return { kind: "error", message: sentence };
    }
    if (err instanceof ApiError && err.status === 500 && typeof details.command === "string") {
      return { kind: "open_failed", command: details.command };
    }
    return { kind: "error", message: errorMessage(err) };
  }

  return (
    <div className="settings-actions">
      <button type="button" className="btn" disabled={busy} onClick={() => void open()}>
        {text("在终端打开开发会话", "Open a development session in the terminal")}
      </button>
      <span className="settings-helper">
        {text(`会在 ${terminalName} 中打开（终端应用在「通用」里换）。`, `Opens in ${terminalName} (change the terminal app under General).`)}
      </span>
      {note?.kind === "busy" && <span className="settings-helper" role="status">{text("正在打开终端…", "Opening the terminal…")}</span>}
      {note?.kind === "opened" && (
        <span className="settings-helper" role="status">
          {text("已在终端打开 ✓ 直接告诉它要修什么、改什么就行。", "Opened in the terminal ✓ — just tell it what to fix or change.")}
        </span>
      )}
      {note?.kind === "open_failed" && (
        <span className="settings-warning" role="alert">
          <span>{text("打开终端失败——去「通用」检查终端应用设置，或手动在终端运行：", "Couldn't open the terminal — check the terminal app under General, or run this by hand: ")}</span>
          <CopyLine label="" value={note.command} />
        </span>
      )}
      {note?.kind === "error" && <span className="settings-warning" role="alert">{note.message}</span>}
    </div>
  );
}
