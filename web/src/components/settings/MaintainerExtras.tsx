// 开发者 · 开发会话 区的动作行（§68.1；原生 SettingsMaintainer.openSession）：「在终端打开开发会话」→
// POST /api/maintainer/terminal（server 读 effective 的仓库路径 / 会话 id 拼命令，客户端零参数）；
// 忙态「正在打开终端…」；server 400 路径不存在 → 「路径不存在」，其它错误原文。
import { useState } from "react";
import { ApiError, postMaintainerTerminal } from "../../api";
import { useI18n } from "../../i18n";
import { errorMessage } from "./useToast";

export function MaintainerExtras() {
  const { text } = useI18n();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; message: string } | null>(null);

  async function open() {
    setBusy(true);
    setNote({ ok: true, message: text("正在打开终端…", "Opening the terminal…") });
    try {
      const receipt = await postMaintainerTerminal();
      setNote({ ok: true, message: receipt.command });
    } catch (err) {
      const pathMissing = err instanceof ApiError && err.status === 400 && /path does not exist/.test(err.message);
      setNote({ ok: false, message: pathMissing ? text("路径不存在", "Path doesn't exist") : errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings-actions">
      <button type="button" className="btn" disabled={busy} onClick={() => void open()}>
        {text("在终端打开开发会话", "Open a development session in the terminal")}
      </button>
      <span className="settings-helper">{text("在 Terminal.app 中打开：cd 仓库目录 && claude（填了会话 id 就 --resume）。", "Opens in Terminal.app: cd into the repo && claude (with --resume when a session id is set).")}</span>
      {note && <span className={note.ok ? "settings-helper" : "settings-warning"} role={note.ok ? "status" : "alert"}>{note.message}</span>}
    </div>
  );
}
