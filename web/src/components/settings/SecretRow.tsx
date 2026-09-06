// 凭证行（原生 CredentialRowView + KeyProbe 的 web 版，§19 / §68.3；文案逐字镜像 Settings.swift:1900–2210）：
// 状态章（未设置 / 使用旧路径 / 已保存（未验证）/ 已验证 ✓ / 验证失败；字幕两把 key 亦是「已保存（未验证）」）+
// 密码框 + 保存 + 验证。**值 write-only**：保存后输入框清空、页面只看见「已保存」；server 永不回显。
// 原生「保存即验证」：可验证的 key 保存成功后自动跑一次验证（已保存，验证中… → 已保存 ✓ 验证通过 /
// 已保存，但验证失败：…）。「验证」按钮照原生：框里有字 → 探这个值（粘贴即验证，不落盘，§68.3 `{value}`）；
// 框空 → 探已保存的；两者都没有 → 「先粘贴（或保存）一个凭证再验证」（Slack 区换成它自己的那句）。
// Gmail 的探针要地址：本地先看设置目录里的 `gmail_address`（原生 effectiveGmailAddress），没填就不发探针、
// 说「还没填 Gmail 地址——在上面「Gmail 地址」填好后点「验证」。」；目录没到本地（SetupPage）就交给 server 判，回执
// `extra.precondition = "gmail_address"` = 同一件事、同一句、章不翻（不是凭证的判决）；应用密码去掉全部空白再存 / 再探（audit 6.4）。
// 验证 = server 侧最小活探针（Anthropic /v1/models、Slack auth.test、Gmail IMAP LOGIN），回执三分（原生 KeyProbe.Outcome）：
// ok → 「验证通过 ✓」（Slack 探已保存的 token 成功 = 原生 SettingsSlack 的「已验证 ✓ 已连接 <team>，身份 @<user> 自动填好——
// 不用再改任何文件。」+ 通知 SlackDirectoryPicker 带 refresh 重载一次）；凭证错（network:false）→ 章「验证失败」+ server
// 的分类人话 `reason {zh,en}`（原生 humanAuthReason；没有就 detail 原文）；判决未知（network:true）→ **章不翻**（原生
// handleOutcome(.failed)：state 3/4 退回 2 = 「已保存（未验证）」）+ 橙色「无法验证（网络/服务问题），稍后点「验证」重试：」+ detail。
// 火山两把 key 没有 server 探针（verifiable:false）——壳在场时给「检测」（CaptionKeyProbe，经桥；§68.2 追记）；
// 它们不是原生的 .plain 行：章是「已保存（未验证）」，保存句尾随「——点「检测」可真连服务器验证一次」，
// 豆包语音凭证按 server 回执 `legacy_pair`（§68.3 2026-09-05 追记）说「已保存 ✓（识别为旧版 App ID + Access Token）」。
// 保存路上的原生把关（同一追记）：Slack 行 xoxb- Bot token 门口拒绝、永不 PUT（SettingsSlack.swift saveToken）；
// 非 xoxp- 与 Gmail 非 16 位字母数字只给橙色提示、照常保存并验证；Enter（非输入法组字中）= 点「保存」，同一道闸。
import { useState } from "react";
import { ApiError, putSecret, verifySecret } from "../../api";
import { useI18n } from "../../i18n";
import { markSlackTokenVerified, refreshSecrets, refreshSettingsCatalog, refreshSetup, useAppState } from "../../store";
import type { BilingualText, SecretStatus, SecretVerifyResult } from "../../types";
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
const SLACK = "slack-user-token.txt";
/** 火山两把 key：无 server 探针，壳的 CaptionKeyProbe 是唯一真连一次服务器的检测 */
export const CAPTION_KEYS = new Set(["volcano-speech-key.txt", "volcano-ark-key.txt"]);

/** 原生 looksLikeAppPassword：16 位字母 / 数字（Google 应用密码的形状）——只提示，不拦 */
export function looksLikeAppPassword(s: string): boolean {
  return /^[\p{L}\p{N}]{16}$/u.test(s);
}

/** suffix = 紧跟 message、无分隔的尾句（原生 `L(a) + L(b)` 拼句：两段各是一条清单标签，各占一个节点） */
type Note = { ok: boolean; message: string; detail?: string; suffix?: string };

export function SecretRow({ name, labelOverride, links = [], helper, placeholderOverride, emptyVerifyNote }: SecretRowProps) {
  const { text, language } = useI18n();
  const { secrets, settingsCatalog } = useAppState();
  const status: SecretStatus | undefined = secrets?.secrets.find((s) => s.name === name);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState<"save" | "verify" | null>(null);
  const [note, setNote] = useState<Note | null>(null);
  const [headsUp, setHeadsUp] = useState<string | null>(null); // 原生的橙色「提示：…仍会尝试验证…」，与保存 / 验证句并存
  const [verified, setVerified] = useState<boolean | null>(null); // 本会话内最近一次验证结果（原生 state 3 / 4）

  const label = labelOverride ?? (status ? pickText(status.label, language) : name);
  const present = status?.present ?? false;
  const legacy = status?.legacy === true;
  const verifiable = status?.verifiable ?? false;
  const isGmail = name === GMAIL;
  const isSlack = name === SLACK;
  const isCaption = CAPTION_KEYS.has(name);
  /** 原生 kind != .plain：有 server 探针或有壳的「检测」——章说「已保存（未验证）」而不是「App 内管理」 */
  const testable = verifiable || isCaption;

  /** 原生 audit 6.4：Google 把应用密码显示成 "abcd efgh ijkl mnop"，内部空白从不是密码的一部分 */
  const normalize = (raw: string): string => (isGmail ? raw.replace(/\s+/g, "") : raw.trim());

  /** 原生 saveToken / save() 存后的橙色提示（形状不对也照常保存并验证）；null = 形状没问题 */
  function shapeHeadsUp(token: string): string | null {
    if (isSlack && !token.startsWith("xoxp-")) {
      return text("提示：User OAuth Token 通常以 xoxp- 开头——检查是否复制对了。仍会尝试验证…", "Heads-up: User OAuth Tokens usually start with xoxp- — double-check the copy. Verifying anyway…");
    }
    if (isGmail && !looksLikeAppPassword(token)) {
      return text("提示：应用密码通常是 16 位字母——检查是否粘贴了别的东西。仍会尝试验证…", "Heads-up: app passwords are usually 16 letters — check you pasted the right thing. Verifying anyway…");
    }
    return null;
  }

  /** 原生 effectiveGmailAddress：设置目录 gmail 区 `gmail_address` 的生效值（目录没到 = 交给 server 判） */
  function gmailAddressMissing(): boolean {
    const field = settingsCatalog?.sections.find((s) => s.id === "gmail")?.fields.find((f) => f.key === "gmail_address");
    return field !== undefined && !String(field.effective ?? "").trim();
  }

  /** 原生 runVerify(.gmail) 探针前的 guard 句：橙色、不是判决、章不动 */
  function noteGmailAddressMissing(afterSave: boolean): void {
    setNote({
      ok: false,
      message: afterSave ? text("已保存，但还没填 Gmail 地址——", "Saved, but no Gmail address yet — ") : text("还没填 Gmail 地址——", "No Gmail address yet — "),
      detail: text("在上面「Gmail 地址」填好后点「验证」。", "fill in \"Gmail address\" above, then click Verify."),
    });
  }

  /** 一次探针（server）。afterSave = 「已保存，」前缀（保存即验证）；candidate 给了 = 粘贴即验证 */
  async function probe(afterSave: boolean, candidate?: string): Promise<void> {
    if (isGmail && gmailAddressMissing()) {
      noteGmailAddressMissing(afterSave);
      return;
    }
    const result = candidate ? await verifySecret(name, candidate) : await verifySecret(name);
    if (result.extra.precondition === "gmail_address") {
      // 目录没到本地时（SetupPage 不挂 CatalogSection）server 替本地判了同一件事：探针没跑、不是凭证的判决——同一句橙句，章不翻
      noteGmailAddressMissing(afterSave);
      return;
    }
    if (result.ok) {
      setVerified(true);
      const identity = !candidate ? slackIdentity(result) : null;
      if (identity) {
        // 原生 SettingsSlack.verifyToken .ok：身份句 + token freshly working → 勾选器带 refresh 重载；server 已把 user_id 写进
        // owner_slack_user_id（只在探已保存的值时——粘贴即验证不落盘也不回填，所以那条路不说「自动填好」），目录刷新让字段跟上
        setNote({ ok: true, message: identity });
        markSlackTokenVerified();
        void refreshSettingsCatalog();
        return;
      }
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

  /** 原生 SettingsSlack.verifyToken 的成功句：auth.test 的 team / user 都在才组，缺一个就回落通用句 */
  function slackIdentity(result: SecretVerifyResult): string | null {
    if (!isSlack) return null;
    const team = result.extra.team;
    const user = result.extra.user;
    if (typeof team !== "string" || !team || typeof user !== "string" || !user) return null;
    return text(`已验证 ✓ 已连接 ${team}，身份 @${user} 自动填好——不用再改任何文件。`, `Verified ✓ Connected to ${team}; identity @${user} filled in automatically — no files to edit.`);
  }

  async function save(next: string) {
    const token = normalize(next);
    setHeadsUp(null);
    if (isSlack && token.startsWith("xoxb-")) {
      // 原生 saveToken：Bot token 能过 auth.test 却读不了你的 DM——门口拒绝，永不落盘（server 同一道门，§68.3 追记）
      setNote({ ok: false, message: text("这是 Bot token（xoxb-）——雷达读你的 DM 需要 User OAuth Token（xoxp- 开头，在 OAuth & Permissions 页的 User 区）。", "That's a Bot token (xoxb-) — reading your DMs needs the User OAuth Token (starts with xoxp-, in the User section of OAuth & Permissions).") });
      return;
    }
    setBusy("save");
    setNote(null);
    setVerified(null);
    try {
      const receipt = await putSecret(name, token);
      setValue("");
      await Promise.all([refreshSecrets(), refreshSetup()]);
      if (!token) {
        setNote({ ok: true, message: text("已清除", "Cleared") });
      } else if (verifiable) {
        setHeadsUp(shapeHeadsUp(token));
        setNote({ ok: true, message: text("已保存，验证中…", "Saved — verifying…") });
        setBusy("verify");
        await probe(true);
      } else if (isCaption) {
        // 原生 isVolcano：保存 = 只存本机不联网，句尾指向「检测」；豆包语音凭证按 server 回执说识别出了旧版对
        const saved = receipt.legacy_pair === true
          ? text("已保存 ✓（识别为旧版 App ID + Access Token）", "Saved ✓ (detected legacy App ID + Access Token)")
          : text("已保存 ✓", "Saved ✓");
        setNote({ ok: true, message: saved, suffix: text("——点「检测」可真连服务器验证一次", " — click Test for one real server check") });
      } else {
        setNote({ ok: true, message: text("已保存 ✓", "Saved ✓") });
      }
    } catch (err) {
      setNote({ ok: false, message: text("保存失败: ", "Save failed: "), detail: saveFailureDetail(err) });
    } finally {
      setBusy(null);
    }
  }

  /** server 400 带 `details.reason {zh,en}`（§68.3 追记：绕过 UI 预检的 xoxb- 也被 server 拒）→ 按 UI 语言取原句；否则 server 整句原文 */
  function saveFailureDetail(err: unknown): string {
    const reason = err instanceof ApiError ? (err.details as { reason?: BilingualText } | undefined)?.reason : undefined;
    const picked = reason && typeof reason === "object" ? pickText(reason, language) : "";
    return picked || errorMessage(err);
  }

  /** 「保存」按钮的闸（原生 .disabled(validating || input 空)）——Enter 走同一道 */
  const canSave = busy === null && value.trim() !== "";

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

  // 原生 stateText：未设置 / 使用旧路径 / 已保存（未验证）/ 已验证 ✓ / 验证失败。目录里五行都 testable，
  // 末尾的「已保存（App 内管理）」是原生 Kind.plain 的词（从不渲染，清单条 retired，§68.3 2026-09-05 追记）——
  // 只作未知名字、既无探针也无「检测」时的兜底，不是任何已知行的状态。
  const stateText = verified === true ? text("已验证 ✓", "verified ✓")
    : verified === false ? text("验证失败", "verification failed")
      : !present ? (legacy ? text("使用旧路径", "Using legacy path") : text("未设置", "Not set"))
        : testable ? text("已保存（未验证）", "saved (not verified)") : text("已保存（App 内管理）", "Saved (managed in-app)");

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
          onChange={(event) => { setValue(event.target.value); if (event.target.value) { setNote(null); setHeadsUp(null); } }}
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
      {headsUp && <p className="settings-warning" role="status">{headsUp}</p>}
      {note && (
        <p className={note.ok ? "settings-helper is-ok" : "settings-warning"} role={note.ok ? "status" : "alert"}>
          <span>{note.message}</span>{note.suffix ? <span>{note.suffix}</span> : null}{note.detail ? <span>{note.ok ? " · " : ""}{note.detail}</span> : null}
        </p>
      )}
    </div>
  );
}
