// 「Claude Code 全局默认」一行（§57 D22 (d)）：显示 ~/.claude/settings.json 的 model，
// 提供显式一键「设为 <id>」（原生 <dialog> 确认 → POST；server 只改 model 键、先备份）。
// 启动时永不改写那个文件——改动只发生在 owner 点确认之后。
import { useState } from "react";
import { useI18n } from "../../i18n";
import type { ClaudeCodeDefault } from "../../types";
import { ModalDialog } from "../board/ModalDialog";

export interface ClaudeCodeDefaultRowProps {
  current: ClaudeCodeDefault | null;
  canonical: string[];
  isBusy: boolean;
  /** 上抛语义动作：把全局默认设为 id（父组件负责请求 + toast） */
  onSetDefault: (model: string) => void;
}

export function ClaudeCodeDefaultRow({ current, canonical, isBusy, onSetDefault }: ClaudeCodeDefaultRowProps) {
  const { text } = useI18n();
  const [choice, setChoice] = useState<string>(canonical[0] ?? "");
  const [isConfirming, setConfirming] = useState(false);

  const model = current?.model ?? null;
  const shown = model ?? text("未设置（CLI 内置默认）", "unset (CLI built-in default)");
  const unreadable = Boolean(current && current.exists && !current.parseable);
  const nonCanonical = Boolean(model && current && !current.canonical);
  const sameAsCurrent = model !== null && model === choice;

  return (
    <div className="settings-global">
      <div className="settings-global-line">
        <span className="settings-global-label">{text("Claude Code 全局默认", "Claude Code global default")}</span>
        <code className="settings-global-value">{shown}</code>
        {current?.path && <span className="settings-global-path">{current.path}</span>}
      </div>
      {unreadable && (
        <p className="settings-warning" role="alert">
          {text(
            "~/.claude/settings.json 不是合法 JSON——读不出全局默认，这里也不会去碰它；请手动修好。",
            "~/.claude/settings.json is not valid JSON — the global default is unreadable and nothing here will touch the file; fix it by hand.",
          )}
        </p>
      )}
      {nonCanonical && !unreadable && (
        <p className="settings-warning">
          {text(
            `「${model}」不是 canonical id：两把旋钮里凡是「跟随」的，都继承它——别名下线那天会一起失败。`,
            `"${model}" is not a canonical id: every knob set to "follow" inherits it — the day the alias retires they all fail together.`,
          )}
        </p>
      )}
      <div className="settings-global-actions">
        <select
          className="settings-select"
          aria-label={text("要设为的全局默认", "Global default to set")}
          value={choice}
          onChange={(event) => setChoice(event.target.value)}
          disabled={isBusy || unreadable}
        >
          {canonical.map((id) => (
            <option key={id} value={id}>{id}</option>
          ))}
        </select>
        <button
          type="button"
          className="btn"
          disabled={isBusy || unreadable || !choice || sameAsCurrent}
          onClick={() => setConfirming(true)}
        >
          {text(`设为 ${choice}`, `Set to ${choice}`)}
        </button>
      </div>
      <p className="settings-helper">
        {text(
          "只改 settings.json 的 model 一个键，其它键原样保留；改之前先在同目录留一份 settings.json.bak-<时间戳> 备份。",
          "Edits only the model key of settings.json and keeps every other key; a settings.json.bak-<timestamp> copy is written next to it first.",
        )}
      </p>
      {isConfirming && (
        <ModalDialog
          title={text("改 Claude Code 全局默认？", "Change the Claude Code global default?")}
          onCancel={() => setConfirming(false)}
        >
          <p className="dialog-body">
            {text(
              `${shown} → ${choice}\n\n这会影响你在终端里的每一次 claude，以及本产品里所有「跟随」的调用。文件会先备份。`,
              `${shown} → ${choice}\n\nThis affects every claude you run in a terminal and every "follow" call in this product. The file is backed up first.`,
            )}
          </p>
          <div className="dialog-actions">
            <button type="button" className="btn" onClick={() => setConfirming(false)}>
              {text("取消", "Cancel")}
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                setConfirming(false);
                onSetDefault(choice);
              }}
            >
              {text("设为该模型", "Set it")}
            </button>
          </div>
        </ModalDialog>
      )}
    </div>
  );
}
