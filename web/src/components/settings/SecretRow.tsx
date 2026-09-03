// 凭证行（原生 CredentialRowView + KeyProbe 的 web 版，§19 / §68）：状态章（已保存 / 未设置）+
// 密码框 + 保存 + 验证。**值 write-only**：保存后输入框清空、页面只看见「已保存」；server 永不回显。
// 验证 = server 侧最小活探针（Anthropic /v1/models、Slack auth.test、Gmail IMAP LOGIN）；
// 网络错与凭证错分开说；火山两把 key 没有探针（verifiable:false）不显示验证按钮。
import { useState } from "react";
import { putSecret, verifySecret } from "../../api";
import { useI18n } from "../../i18n";
import { refreshSecrets, refreshSetup, useAppState } from "../../store";
import type { SecretStatus } from "../../types";
import { pickText } from "./catalogText";
import { errorMessage } from "./useToast";

export interface SecretRowProps {
  name: string;
  /** 覆盖 server label（如来源区想写「Slack user token（xoxp-…）」）；缺省用 server 目录 */
  labelOverride?: string;
  /** 帮助链接（控制台 / 设置文档） */
  links?: Array<{ label: string; href: string }>;
  helper?: string;
}

export function SecretRow({ name, labelOverride, links = [], helper }: SecretRowProps) {
  const { text, language } = useI18n();
  const { secrets } = useAppState();
  const status: SecretStatus | undefined = secrets?.secrets.find((s) => s.name === name);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState<"save" | "verify" | null>(null);
  const [note, setNote] = useState<{ ok: boolean; message: string } | null>(null);

  const label = labelOverride ?? (status ? pickText(status.label, language) : name);
  const present = status?.present ?? false;

  async function save(next: string) {
    setBusy("save");
    setNote(null);
    try {
      await putSecret(name, next);
      setValue("");
      await Promise.all([refreshSecrets(), refreshSetup()]);
      setNote({ ok: true, message: next ? text("已保存（值不会再显示）", "Saved (the value is never shown again)") : text("已清除", "Cleared") });
    } catch (err) {
      setNote({ ok: false, message: errorMessage(err) });
    } finally {
      setBusy(null);
    }
  }

  async function verify() {
    setBusy("verify");
    setNote(null);
    try {
      const result = await verifySecret(name);
      const prefix = result.ok ? text("验证通过：", "Verified: ") : result.network ? text("网络不通（不是凭证的问题）：", "Network error (not the credential): ") : text("验证失败：", "Verification failed: ");
      setNote({ ok: result.ok, message: prefix + result.detail });
    } catch (err) {
      setNote({ ok: false, message: errorMessage(err) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="settings-secret" data-secret={name}>
      <div className="settings-field-head">
        <span className="settings-knob-label">{label}</span>
        <span className={`settings-source-chip${present ? " is-present" : ""}`}>
          {present ? text("已保存", "Saved") : text("未设置", "Not set")}
        </span>
        {links.map((link) => (
          <a key={link.href} className="settings-link" href={link.href} target="_blank" rel="noreferrer">{link.label}</a>
        ))}
      </div>
      <div className="settings-knob-controls">
        <input
          type="password"
          className="settings-input"
          aria-label={text(`${label} 的值`, `${label} value`)}
          placeholder={present ? text("粘贴新值以替换…", "Paste a new value to replace…") : text("粘贴到这里…", "Paste here…")}
          value={value}
          disabled={busy !== null}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => setValue(event.target.value)}
        />
        <button type="button" className="btn btn-primary" disabled={busy !== null || !value.trim()} onClick={() => void save(value)}>
          {busy === "save" ? text("保存中…", "Saving…") : text("保存", "Save")}
        </button>
        {status?.verifiable && (
          <button type="button" className="btn" disabled={busy !== null || !present} onClick={() => void verify()}>
            {busy === "verify" ? text("验证中…", "Verifying…") : text("验证", "Verify")}
          </button>
        )}
        {present && (
          <button type="button" className="btn btn-danger btn-quiet" disabled={busy !== null} onClick={() => void save("")}>
            {text("清除", "Clear")}
          </button>
        )}
      </div>
      {helper && <p className="settings-helper">{helper}</p>}
      {note && <p className={note.ok ? "settings-helper is-ok" : "settings-warning"} role={note.ok ? "status" : "alert"}>{note.message}</p>}
    </div>
  );
}
