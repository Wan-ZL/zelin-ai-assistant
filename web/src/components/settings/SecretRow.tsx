// 凭证行（原生 CredentialRowView + KeyProbe 的 web 版，§19 / §68.3；文案逐字镜像 Settings.swift:1900–2210）：
// 状态章（未设置 / 使用旧路径 / 已保存（未验证）/ 已验证 ✓ / 验证失败；无探针的 key = 已保存（App 内管理））+
// 密码框 + 保存 + 验证。**值 write-only**：保存后输入框清空、页面只看见「已保存」；server 永不回显。
// 原生「保存即验证」：可验证的 key 保存成功后自动跑一次验证（已保存，验证中… → 已保存 ✓ 验证通过 /
// 已保存，但验证失败：…）。「验证」按钮照原生：框里有字 → 探这个值（粘贴即验证，不落盘，§68.3 `{value}`）；
// 框空 → 探已保存的；两者都没有 → 「先粘贴（或保存）一个凭证再验证」（Slack 区换成它自己的那句）。
// Gmail 的探针要地址：本地先看设置目录里的 `gmail_address`（原生 effectiveGmailAddress），没填就不发探针、
// 说「还没填 Gmail 地址——在上面「Gmail 地址」填好后点「验证」。」；应用密码去掉全部空白再存 / 再探（audit 6.4）。
// 验证 = server 侧最小活探针（Anthropic /v1/models、Slack auth.test、Gmail IMAP LOGIN）；网络错与凭证错分开说；
// 火山两把 key 没有 server 探针（verifiable:false）——壳在场时给「检测」（CaptionKeyProbe，经桥；§68.2 追记）。
import { useState } from "react";
import { putSecret, verifySecret } from "../../api";
import { useI18n } from "../../i18n";
import { refreshSecrets, refreshSetup, useAppState } from "../../store";
import type { SecretStatus } from "../../types";
import { CaptionKeyTest } from "./CaptionKeyTest";
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
  /** 框空且没保存过时点「验证」的提示（Slack 区：先粘贴并保存 token 再验证） */
  emptyVerifyNote?: string;
}

const GMAIL = "gmail-app-password.txt";
/** 火山两把 key：无 server 探针，壳的 CaptionKeyProbe 是唯一真连一次服务器的检测 */
export const CAPTION_KEYS = new Set(["volcano-speech-key.txt", "volcano-ark-key.txt"]);

type Note = { ok: boolean; message: string; detail?: string };

export function SecretRow({ name, labelOverride, links = [], helper, placeholderOverride, emptyVerifyNote }: SecretRowProps) {
  const { text, language } = useI18n();
  const { secrets, settingsCatalog } = useAppState();
  const status: SecretStatus | undefined = secrets?.secrets.find((s) => s.name === name);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState<"save" | "verify" | null>(null);
  const [note, setNote] = useState<Note | null>(null);
  const [verified, setVerified] = useState<boolean | null>(null); // 本会话内最近一次验证结果（原生 state 3 / 4）

  const label = labelOverride ?? (status ? pickText(status.label, language) : name);
  const present = status?.present ?? false;
  const legacy = status?.legacy === true;
  const verifiable = status?.verifiable ?? false;
  const isGmail = name === GMAIL;

  /** 原生 audit 6.4：Google 把应用密码显示成 "abcd efgh ijkl mnop"，内部空白从不是密码的一部分 */
  const normalize = (raw: string): string => (isGmail ? raw.replace(/\s+/g, "") : raw.trim());

  /** 原生 effectiveGmailAddress：设置目录 gmail 区 `gmail_address` 的生效值（目录没到 = 交给 server 判） */
  function gmailAddressMissing(): boolean {
    const field = settingsCatalog?.sections.find((s) => s.id === "gmail")?.fields.find((f) => f.key === "gmail_address");
    return field !== undefined && !String(field.effective ?? "").trim();
  }

  /** 一次探针（server）。afterSave = 「已保存，」前缀（保存即验证）；candidate 给了 = 粘贴即验证 */
  async function probe(afterSave: boolean, candidate?: string): Promise<void> {
    if (isGmail && gmailAddressMissing()) {
      setNote({
        ok: false,
        message: afterSave ? text("已保存，但还没填 Gmail 地址——", "Saved, but no Gmail address yet — ") : text("还没填 Gmail 地址——", "No Gmail address yet — "),
        detail: text("在上面「Gmail 地址」填好后点「验证」。", "fill in \"Gmail address\" above, then click Verify."),
      });
      return;
    }
    const result = candidate ? await verifySecret(name, candidate) : await verifySecret(name);
    setVerified(result.ok);
    if (result.ok) {
      setNote({ ok: true, message: afterSave ? text("已保存 ✓ 验证通过", "Saved ✓ verified") : text("验证通过 ✓", "Verified ✓"), detail: result.detail });
      return;
    }
    const why = (result.network ? text("网络不通（不是凭证的问题）：", "Network error (not the credential): ") : "") + result.detail;
    setNote({ ok: false, message: afterSave ? text("已保存，但验证失败：", "Saved, but verification FAILED: ") : text("验证失败：", "Verification failed: "), detail: why });
  }

  async function save(next: string) {
    const token = normalize(next);
    setBusy("save");
    setNote(null);
    setVerified(null);
    try {
      await putSecret(name, token);
      setValue("");
      await Promise.all([refreshSecrets(), refreshSetup()]);
      if (!token) {
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

  /** 原生 verify()：框里有字探它（先验再决定存不存），框空探已保存的，都没有就提示 */
  async function verify() {
    const candidate = normalize(value);
    if (!candidate && !present) {
      setNote({ ok: false, message: emptyVerifyNote ?? text("先粘贴（或保存）一个凭证再验证", "Paste (or save) a credential first") });
      return;
    }
    setBusy("verify");
    setNote(null);
    try {
      await probe(false, candidate || undefined);
    } catch (err) {
      setVerified(false);
      setNote({ ok: false, message: text("验证失败：", "Verification failed: "), detail: errorMessage(err) });
    } finally {
      setBusy(null);
    }
  }

  // 原生 stateText：未设置 / 使用旧路径 / 已保存（未验证）/ 已保存（App 内管理）/ 已验证 ✓ / 验证失败
  const stateText = verified === true ? text("已验证 ✓", "verified ✓")
    : verified === false ? text("验证失败", "verification failed")
      : !present ? (legacy ? text("使用旧路径", "Using legacy path") : text("未设置", "Not set"))
        : verifiable ? text("已保存（未验证）", "saved (not verified)") : text("已保存（App 内管理）", "Saved (managed in-app)");

  return (
    <div className="settings-secret" data-secret={name}>
      <div className="settings-field-head">
        <span className="settings-knob-label">{label}</span>
        <span className={`settings-source-chip${present || legacy ? " is-present" : ""}${verified === false ? " is-failed" : ""}`}>
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
          onChange={(event) => { setValue(event.target.value); if (event.target.value) setNote(null); }}
        />
        <button type="button" className="btn btn-primary" disabled={busy !== null || !value.trim()} onClick={() => void save(value)}>
          {busy === "save" ? text("保存中…", "Saving…") : text("保存", "Save")}
        </button>
        {verifiable && (
          <button type="button" className="btn" disabled={busy !== null} onClick={() => void verify()}>
            {busy === "verify" ? text("验证中…", "Verifying…") : text("验证", "Verify")}
          </button>
        )}
        {CAPTION_KEYS.has(name) && (
          <CaptionKeyTest name={name} value={value} disabled={busy !== null} onVerdict={(ok) => setVerified(ok)} />
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
