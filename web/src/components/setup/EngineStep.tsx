// 向导第 2 步「接入 AI 引擎」（原生 SetupWizard.swift engineStep / EngineDetector / KeyProbe 的 web 版，§68.5）：
// GET /api/setup/engine 检测 claude CLI + 认证梯子（Claude Code 登录 / ANTHROPIC_API_KEY / App 内保存的 key /
// 旧路径）——就绪 = 「已连接,无需配置」+ 版本 + 认证方式；否则 A. 用 Claude Code 登录（装 / 登录 + 重新检测）
// 或 B. 粘贴 Anthropic API key：**先验后存**（POST /api/secrets/…/verify {value} 只探不落盘，有效才 PUT
// 保存到 config/secrets/anthropic-api-key.txt，0600）——无效的 key 永不静默存下；网络不通时「保存」可先存
// 稍后再验。文案逐字镜像 SetupWizard.swift:672–852。
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchSetupEngine, putSecret, verifySecret } from "../../api";
import { useI18n } from "../../i18n";
import { refreshSecrets, refreshSetup } from "../../store";
import type { SetupEngine } from "../../types";
import { copyText } from "../detail/copyText";
import { errorMessage } from "../settings/useToast";

type Text = (zh: string, en: string) => string;
const KEY_NAME = "anthropic-api-key.txt";
const PASTE_DEBOUNCE_MS = 600;
const MIN_KEY_LEN = 20;

/** 认证梯子的人话（原生 EngineAuth.label；顺序 = server AUTH_LADDER） */
export const AUTH_LABELS: Array<[key: string, zh: string, en: string]> = [
  ["oauth", "Claude Code 登录", "Claude Code login"],
  ["env_key", "ANTHROPIC_API_KEY 环境变量", "ANTHROPIC_API_KEY env var"],
  ["secrets_file", "API key(App 内保存)", "API key (saved in-app)"],
  ["legacy_file", "API key(旧路径)", "API key (legacy path)"],
];

export function authLabel(auth: string | null | undefined, text: Text): string {
  const hit = AUTH_LABELS.find(([key]) => key === auth);
  return hit ? text(hit[1], hit[2]) : "";
}

/** 检测状态的小仓：页面级 hook，向导第 2 步与末步「AI 引擎」行共用同一份。
 *  `quiet`：末步定时复检用——不翻 `checking`（行不闪成「检测中…」），结果照样落下（红行会自己变绿）。 */
export function useEngineDetector() {
  const [engine, setEngine] = useState<SetupEngine | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const detect = useCallback(async (opts: { quiet?: boolean } = {}) => {
    if (!opts.quiet) {
      setChecking(true);
      setError(null);
    }
    try {
      setEngine(await fetchSetupEngine());
      if (opts.quiet) setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      if (!opts.quiet) setChecking(false);
    }
  }, []);
  useEffect(() => {
    void detect();
  }, [detect]);
  return { engine, checking, error, detect };
}

function CopyLine({ label, value }: { label: string; value: string }) {
  const { text } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <p className="perm-path">
      <span>{label}</span>
      <code>{value}</code>
      <button type="button" className="zai-detail-copy" onClick={() => void copyText(value).then((ok) => { setCopied(ok); if (ok) window.setTimeout(() => setCopied(false), 1500); })}>
        {copied ? text("已复制", "Copied") : text("复制", "Copy")}
      </button>
    </p>
  );
}

function AuthLadder({ engine }: { engine: SetupEngine }) {
  const { text } = useI18n();
  return (
    <ul className="setup-ladder" aria-label={text("认证方式", "Auth")}>
      {AUTH_LABELS.map(([key, zh, en]) => {
        const hit = engine.auth_sources?.[key] === true;
        return <li key={key} className={hit ? "is-hit" : ""}><span aria-hidden="true">{hit ? "✓" : "○"}</span> <span>{text(zh, en)}</span></li>;
      })}
    </ul>
  );
}

/** 一句提示：前缀 + 明细两个节点（原生 `L("保存失败: ") + error` 拼接；探针按节点逐字判前缀） */
interface KeyNote { message: string; detail?: string; tone: "ok" | "warn" | "error" | "muted" }

/** B. 粘贴 API key：粘贴后自动验证（去抖 600 ms）；「保存」= 验证 → 有效才存；网络不通 → 先存（未验证） */
function KeyPasteCard({ onSaved }: { onSaved: () => void }) {
  const { text } = useI18n();
  const [value, setValue] = useState("");
  const [probing, setProbing] = useState(false);
  const [note, setNote] = useState<KeyNote | null>(null);
  const timer = useRef<number | null>(null);
  const lastProbed = useRef("");

  const save = useCallback(async (key: string, verified: boolean) => {
    try {
      await putSecret(KEY_NAME, key);
      setValue("");
      await Promise.all([refreshSecrets(), refreshSetup()]);
      setNote(verified
        ? { message: text("✅ key 有效,已保存", "✅ Key valid — saved"), tone: "ok" }
        : { message: text("已保存(未验证)——之后可在 设置 → 凭证 里点「验证」", "Saved (unverified) — you can Verify later in Settings → Credentials"), tone: "muted" });
      onSaved();
    } catch (err) {
      setNote({ message: verified ? text("key 有效,但保存失败: ", "Key valid, but saving failed: ") : text("保存失败: ", "Save failed: "), detail: errorMessage(err), tone: "error" });
    }
  }, [onSaved, text]);

  /** 先验后存（原生 verifyAndSaveKey）：无效不存；网络错留给「保存」按钮 */
  const verifyThenSave = useCallback(async (key: string, manual: boolean) => {
    setProbing(true);
    lastProbed.current = key;
    setNote({ message: text("验证中…", "Verifying…"), tone: "muted" });
    try {
      const result = await verifySecret(KEY_NAME, key);
      if (result.ok) {
        await save(key, true);
      } else if (result.network) {
        if (manual) await save(key, false);
        else setNote({ message: text(`暂时无法验证(网络问题): ${result.detail}——可点「保存」先存下,稍后在 设置 → 凭证 里再验证`, `Couldn't verify right now (network): ${result.detail} — click Save to store it and re-verify later in Settings → Credentials`), tone: "warn" });
      } else {
        setNote({ message: text("❌ key 无效——请到控制台重新生成一个,回来再粘贴", "❌ Invalid key — regenerate one in the console and paste again"), tone: "error" });
      }
    } catch (err) {
      // 探针本身没跑成（server 4xx / 断连）：与网络错同样处理——手动保存可先存
      if (manual) await save(key, false);
      else setNote({ message: text(`暂时无法验证(网络问题): ${errorMessage(err)}——可点「保存」先存下,稍后在 设置 → 凭证 里再验证`, `Couldn't verify right now (network): ${errorMessage(err)} — click Save to store it and re-verify later in Settings → Credentials`), tone: "warn" });
    } finally {
      setProbing(false);
    }
  }, [save, text]);

  const onChange = (raw: string) => {
    setValue(raw);
    const key = raw.replace(/\s+/g, "");
    if (timer.current) window.clearTimeout(timer.current);
    if (!key) {
      setNote(null);
      return;
    }
    if (key.length < MIN_KEY_LEN || key === lastProbed.current) return;
    timer.current = window.setTimeout(() => {
      if (!probing) void verifyThenSave(key, false);
    }, PASTE_DEBOUNCE_MS);
  };

  const trimmed = value.replace(/\s+/g, "");
  return (
    <div className="setup-card">
      <h4 className="setup-card-title">{text("B. 粘贴 Anthropic API key", "B. Paste an Anthropic API key")}</h4>
      <p className="settings-helper">{text("从控制台生成一个 key 粘贴到这里——粘贴后自动验证(一次免费的连通性检查,不消耗 tokens),有效才保存(仅存本机,权限 0600)。", "Generate a key in the console and paste it here — it verifies on paste (one free connectivity check, no tokens billed) and is saved only when valid (local only, mode 0600).")}</p>
      <div className="settings-knob-controls">
        <input
          type="password"
          className="settings-input"
          aria-label={text("Anthropic API key", "Anthropic API key")}
          placeholder={text("sk-ant-…(粘贴后自动验证)", "sk-ant-… (verifies on paste)")}
          value={value}
          autoComplete="off"
          spellCheck={false}
          disabled={probing}
          onChange={(e) => onChange(e.target.value)}
        />
        <button type="button" className="btn btn-primary" disabled={probing || !trimmed} onClick={() => { if (timer.current) window.clearTimeout(timer.current); void verifyThenSave(trimmed, true); }}>
          {probing ? text("验证中…", "Verifying…") : text("保存", "Save")}
        </button>
        <a className="settings-link" href="https://console.anthropic.com/settings/keys" target="_blank" rel="noreferrer">{text("打开控制台", "Open console")}</a>
      </div>
      {note && (
        <p className={note.tone === "error" || note.tone === "warn" ? "settings-warning" : note.tone === "ok" ? "settings-helper is-ok" : "settings-helper"} role={note.tone === "error" ? "alert" : "status"}>
          <span>{note.message}</span>{note.detail ? <span>{note.detail}</span> : null}
        </p>
      )}
    </div>
  );
}

export function EngineStep({ engine, checking, error, detect }: { engine: SetupEngine | null; checking: boolean; error: string | null; detect: () => Promise<void> }) {
  const { text } = useI18n();
  const redetect = () => void detect();

  return (
    <>
      {checking && !engine ? (
        <div className="setup-card"><p className="settings-helper">{text("正在检测 claude CLI 与登录状态…", "Detecting the claude CLI and its login…")}</p></div>
      ) : error && !engine ? (
        <div className="setup-card">
          <p className="settings-warning" role="alert">{error}</p>
          <div className="settings-actions"><button type="button" className="btn" onClick={redetect}>{text("重新检测", "Re-detect")}</button></div>
        </div>
      ) : engine?.ready ? (
        <div className="setup-card" data-engine="ready">
          <div className="perm-capability-head">
            <span className="perm-dot is-granted" aria-hidden="true" />
            <strong className="settings-helper is-ok">{text("已连接,无需配置", "Connected — nothing to configure")}</strong>
            <span className="perm-capability-action"><button type="button" className="btn" disabled={checking} onClick={redetect}>{text("重新检测", "Re-detect")}</button></span>
          </div>
          {engine.version && <p className="settings-list-dim">{engine.version}</p>}
          <p className="settings-helper"><span>{text("认证方式:", "Auth: ")}</span><span>{authLabel(engine.auth, text)}</span></p>
          <AuthLadder engine={engine} />
        </div>
      ) : (
        <>
          <p className="settings-warning">{text("没有 AI 引擎,提案永远不会被执行。选择下面任一方式接入(推荐 A):", "Without an AI engine, proposals will never be executed. Connect with either option below (A recommended):")}</p>
          <div className="setup-card" data-engine={engine?.cli_path ? "no-auth" : "no-cli"}>
            <h4 className="setup-card-title">{text("A. 使用 Claude Code 现有登录(推荐)", "A. Use your Claude Code login (recommended)")}</h4>
            {!engine?.cli_path ? (
              <>
                <p className="settings-helper">{text("还没找到 claude 命令。安装 Claude Code,登录一次,然后点「重新检测」。", "The claude command wasn't found. Install Claude Code, log in once, then click Re-detect.")}</p>
                <CopyLine label={text("安装命令:", "Install: ")} value="npm install -g @anthropic-ai/claude-code" />
                <div className="settings-actions">
                  <a className="btn" href="https://claude.com/claude-code" target="_blank" rel="noreferrer">{text("打开安装页", "Open install page")}</a>
                  <button type="button" className="btn" disabled={checking} onClick={redetect}>{text("重新检测", "Re-detect")}</button>
                </div>
              </>
            ) : (
              <>
                <p className="settings-helper">{text("已找到 claude CLI,但还没有登录。在终端运行 claude 按提示登录,回来点「重新检测」。", "The claude CLI is installed but not logged in. Run claude in Terminal, follow the login prompt, then click Re-detect.")}</p>
                <CopyLine label={text("在终端运行:", "Run in Terminal: ")} value="claude" />
                <div className="settings-actions"><button type="button" className="btn" disabled={checking} onClick={redetect}>{text("重新检测", "Re-detect")}</button></div>
              </>
            )}
            {engine && <AuthLadder engine={engine} />}
          </div>
          <KeyPasteCard onSaved={redetect} />
          <p className="settings-helper">{text("现在跳过也可以——之后在 设置 → 凭证 里随时补上。", "Skipping now is fine too — add it anytime later in Settings → Credentials.")}</p>
        </>
      )}
    </>
  );
}
