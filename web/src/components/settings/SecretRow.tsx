// 凭证行（原生 CredentialRowView + KeyProbe 的 web 版，§19 / §68.3；文案逐字镜像 Settings.swift:1900–2210）：
// 状态章（未设置 / 使用旧路径 / 已保存（未验证）/ 已验证 ✓ / 验证失败）+ 密码框 + 保存 + 验证。**值 write-only**：保存后
// 输入框清空、页面只看见「已保存」；server 永不回显。
// 原生「保存即验证」：可验证的 key 保存成功后自动跑一次验证（已保存，验证中… → 已保存 ✓ 验证通过 /
// 已保存，但验证失败：…）。「验证」按钮照原生：框里有字 → 探这个值（粘贴即验证，不落盘，§68.3 `{value}`）；
// 框空 → 探已保存的；两者都没有 → 「先粘贴（或保存）一个凭证再验证」。
// 验证 = server 侧最小活探针（Anthropic /v1/models），回执三分（原生 KeyProbe.Outcome）：ok → 「验证通过 ✓」；凭证错
// （network:false）→ 章「验证失败」+ server 的分类人话 `reason {zh,en}`（原生 humanAuthReason；没有就 detail 原文）；判决未知
// （network:true）→ **章不翻**（原生 handleOutcome(.failed)：state 3/4 退回 2 = 「已保存（未验证）」）+ 橙色「无法验证（网络/
// 服务问题），稍后点「验证」重试：」+ detail。Enter（非输入法组字中）= 点「保存」，同一道闸。
// v2.0.0 退役（全文见 tag full）：Slack token / Gmail 应用密码两行的形状把关与身份回填、火山字幕两把 key 的「检测」（经桥）——
// §19 只剩 Anthropic API key。
import { useState } from "react";
import { ApiError, putSecret, verifySecret } from "../../api";
import { useI18n } from "../../i18n";
import { refreshSecrets, refreshSetup, useAppState } from "../../store";
import type { BilingualText, SecretStatus } from "../../types";
import { pickText } from "./catalogText";
import { errorMessage } from "./useToast";

export interface SecretRowProps {
  name: string;
  /** 覆盖 server label；缺省用 server 目录 */
  labelOverride?: string;
  /** 帮助链接（控制台 / 设置文档） */
  links?: Array<{ label: string; href: string }>;
  helper?: string;
  /** 覆盖输入框提示 */
  placeholderOverride?: string;
  /** 框空且没保存过时点「验证」的提示 */
  emptyVerifyNote?: string;
}

/** suffix = 紧跟 message、无分隔的尾句（原生 `L(a) + L(b)` 拼句：两段各是一条清单标签，各占一个节点） */
type Note = { ok: boolean; message: string; detail?: string; suffix?: string };

export function SecretRow({ name, labelOverride, links = [], helper, placeholderOverride, emptyVerifyNote }: SecretRowProps) {
  const { text, language } = useI18n();
  const { secrets } = useAppState();
  const status: SecretStatus | undefined = secrets?.secrets.find((s) => s.name === name);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState<"save" | "verify" | null>(null);
  const [note, setNote] = useState<Note | null>(null);
  const [verified, setVerified] = useState<boolean | null>(null); // 本会话内最近一次验证结果（原生 state 3 / 4）

  const label = labelOverride ?? (status ? pickText(status.label, language) : name);
  const present = status?.present ?? false;
  const legacy = status?.legacy === true;
  const verifiable = status?.verifiable ?? false;
  /** 一次探针（server）。afterSave = 「已保存，」前缀（保存即验证）；candidate 给了 = 粘贴即验证 */
  async function probe(afterSave: boolean, candidate?: string): Promise<void> {
    const result = candidate ? await verifySecret(name, candidate) : await verifySecret(name);
    if (result.ok) {
      setVerified(true);
      setNote({ ok: true, message: afterSave ? text("已保存 ✓ 验证通过", "Saved ✓ verified") : text("验证通过 ✓", "Verified ✓"), detail: result.detail });
      return;
    }
    if (result.network) {
      // 原生 handleOutcome(.failed)：网络 / 服务——凭证的判决未知，章退回「已保存（未验证）」而不是装作失败
      setVerified(null);
      setNote({ ok: false, message: text("无法验证（网络/服务问题），稍后点「验证」重试：", "Couldn't verify (network/service) — click Verify again later: "), detail: result.detail });
      return;
    }
    setVerified(false);
    const reason = pickText(result.reason, language) || result.detail;   // 原生 humanAuthReason 的分类句（server-owned）；老 server 没有就 detail
    setNote({ ok: false, message: afterSave ? text("已保存，但验证失败：", "Saved, but verification FAILED: ") : text("验证失败：", "Verification failed: "), detail: reason });
  }

  async function save(next: string) {
    const token = next.trim();
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
      setNote({ ok: false, message: text("保存失败: ", "Save failed: "), detail: saveFailureDetail(err) });
    } finally {
      setBusy(null);
    }
  }

  /** server 400 带 `details.reason {zh,en}` → 按 UI 语言取原句；否则 server 整句原文 */
  function saveFailureDetail(err: unknown): string {
    const reason = err instanceof ApiError ? (err.details as { reason?: BilingualText } | undefined)?.reason : undefined;
    const picked = reason && typeof reason === "object" ? pickText(reason, language) : "";
    return picked || errorMessage(err);
  }

  /** 「保存」按钮的闸（原生 .disabled(validating || input 空)）——Enter 走同一道 */
  const canSave = busy === null && value.trim() !== "";

  /** 原生 verify()：框里有字探它（先验再决定存不存），框空探已保存的，都没有就提示 */
  async function verify() {
    const candidate = value.trim();
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

  // 原生 stateText：未设置 / 使用旧路径 / 已保存（未验证）/ 已验证 ✓ / 验证失败。末尾的「已保存（App 内管理）」是原生
  // Kind.plain 的词——只作未知名字、没有探针时的兜底，不是任何已知行的状态。
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
          onKeyDown={(event) => {
            // 原生 SecureField .onSubmit { saveToken() }：Enter = 保存；输入法组字中的 Enter 是选字，不算
            if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
            event.preventDefault();
            if (canSave) void save(value);
          }}
        />
        <button type="button" className="btn btn-primary" disabled={!canSave} onClick={() => void save(value)}>
          {busy === "save" ? text("保存中…", "Saving…") : text("保存", "Save")}
        </button>
        {verifiable && (
          <button type="button" className="btn" disabled={busy !== null} onClick={() => void verify()}>
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
          <span>{note.message}</span>{note.suffix ? <span>{note.suffix}</span> : null}{note.detail ? <span>{note.ok ? " · " : ""}{note.detail}</span> : null}
        </p>
      )}
    </div>
  );
}
