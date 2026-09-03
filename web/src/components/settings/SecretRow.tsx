// 凭证行（原生 CredentialRowView + KeyProbe 的 web 版，§19 / §68.3；文案逐字镜像 Settings.swift:1900–2210）：
// 状态章（未设置 / 已保存（未验证）/ 已验证 ✓ / 验证失败；无探针的 key = 已保存（App 内管理））+ 密码框 +
// 保存 + 验证。**值 write-only**：保存后输入框清空、页面只看见「已保存」；server 永不回显。
// 原生「保存即验证」：可验证的 key 保存成功后自动跑一次验证（已保存，验证中… → 已保存 ✓ 验证通过 /
// 已保存，但验证失败：…）。验证 = server 侧最小活探针（Anthropic /v1/models、Slack auth.test、Gmail IMAP LOGIN）；
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
  /** 覆盖输入框提示（Slack 区「xoxp-…（只存本机 config/secrets/）」） */
  placeholderOverride?: string;
}

export function SecretRow({ name, labelOverride, links = [], helper, placeholderOverride }: SecretRowProps) {
  const { text, language } = useI18n();
  const { secrets } = useAppState();
  const status: SecretStatus | undefined = secrets?.secrets.find((s) => s.name === name);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState<"save" | "verify" | null>(null);
  const [note, setNote] = useState<{ ok: boolean; message: string; detail?: string } | null>(null);
  const [verified, setVerified] = useState<boolean | null>(null); // 本会话内最近一次验证结果（原生 state 3 / 4）

  const label = labelOverride ?? (status ? pickText(status.label, language) : name);
  const present = status?.present ?? false;
  const verifiable = status?.verifiable ?? false;

  /** 一次探针；返回 ok。after = 「已保存，」前缀（保存即验证）或空 */
  async function probe(afterSave: boolean): Promise<void> {
    const result = await verifySecret(name);
    setVerified(result.ok);
    if (result.ok) {
      setNote({ ok: true, message: afterSave ? text("已保存 ✓ 验证通过", "Saved ✓ verified") : text("验证通过 ✓", "Verified ✓"), detail: result.detail });
      return;
    }
    const why = (result.network ? text("网络不通（不是凭证的问题）：", "Network error (not the credential): ") : "") + result.detail;
    setNote({ ok: false, message: afterSave ? text("已保存，但验证失败：", "Saved, but verification FAILED: ") : text("验证失败：", "Verification failed: "), detail: why });
  }

  async function save(next: string) {
    setBusy("save");
    setNote(null);
    setVerified(null);
    try {
      await putSecret(name, next);
      setValue("");
      await Promise.all([refreshSecrets(), refreshSetup()]);
      if (!next) {
        setNote({ ok: true, message: text("已清除", "Cleared") });
      } else if (verifiable) {
        setNote({ ok: true, message: text("已保存，验证中…", "Saved — verifying…") });
        setBusy("verify");
        await probe(true);
      } else {
        setNote({ ok: true, message: text("已保存 ✓", "Saved ✓") });
      }
    } catch (err) {
      setNote({ ok: false, message: text("保存失败: ", "Save failed: "), detail: errorMessage(err) });
    } finally {
      setBusy(null);
    }
  }

  async function verify() {
    setBusy("verify");
    setNote(null);
    try {
      await probe(false);
    } catch (err) {
      setVerified(false);
      setNote({ ok: false, message: text("验证失败：", "Verification failed: "), detail: errorMessage(err) });
    } finally {
      setBusy(null);
    }
  }

  // 原生 stateText：未设置 / 已保存（未验证）/ 已保存（App 内管理）/ 已验证 ✓ / 验证失败
  const stateText = !present
    ? text("未设置", "Not set")
    : verified === true ? text("已验证 ✓", "verified ✓")
      : verified === false ? text("验证失败", "verification failed")
        : verifiable ? text("已保存（未验证）", "saved (not verified)") : text("已保存（App 内管理）", "Saved (managed in-app)");

  return (
    <div className="settings-secret" data-secret={name}>
      <div className="settings-field-head">
        <span className="settings-knob-label">{label}</span>
        <span className={`settings-source-chip${present ? " is-present" : ""}${verified === false ? " is-failed" : ""}`}>
          {stateText}
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
          placeholder={placeholderOverride ?? (verifiable ? text("粘贴后点保存（只存本机，保存即验证）", "Paste, then Save (stored locally; verified on save)") : text("粘贴后点保存（只存本机，不联网）", "Paste, then Save (stored locally; no network)"))}
          value={value}
          disabled={busy !== null}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => setValue(event.target.value)}
        />
        <button type="button" className="btn btn-primary" disabled={busy !== null || !value.trim()} onClick={() => void save(value)}>
          {busy === "save" ? text("保存中…", "Saving…") : text("保存", "Save")}
        </button>
        {verifiable && (
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
      {note && (
        <p className={note.ok ? "settings-helper is-ok" : "settings-warning"} role={note.ok ? "status" : "alert"}>
          <span>{note.message}</span>{note.detail ? <span>{note.ok ? " · " : ""}{note.detail}</span> : null}
        </p>
      )}
    </div>
  );
}
