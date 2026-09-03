// 首次运行向导（§15 v0.14 初始设置向导 → §68.5 web 版；?page=setup）。新机器 / 空环境：
// config.yaml 缺席或三把主凭证一把都没有（且没写过完成标记）时，看板开在这里而不是空看板
// （app.tsx 按 GET /api/setup 的 needed 判定跳转）。四步：
//   1 配置文件（从 config.example.yaml 复制，绝不覆盖）→ 2 完全磁盘访问（后台进程的授权清单，
//   读 GET /api/permissions）→ 3 可选凭证（Slack / Gmail / Anthropic，SecretRow 经 server 写 0600）
//   → 4 完成（写 state/setup_done.json；再也不弹，设置 → 关于 可重跑）。
// 幂等：每步预填当前真值、跳过不清数据；中途关掉下次还会回来（标记只在最后一步写）。
import { useEffect, useState } from "react";
import "../components/chrome/chrome.css";
import "../components/settings/settings.css";
import { postSetupStep } from "../api";
import { pickText } from "../components/settings/catalogText";
import { SecretRow } from "../components/settings/SecretRow";
import { errorMessage } from "../components/settings/useToast";
import { useI18n } from "../i18n";
import { buildAppUrl, navigate } from "../route";
import { refreshPermissions, refreshSecrets, refreshSetup, setSetup, useAppState } from "../store";

const STEPS = ["config", "fda", "credentials", "done"] as const;
type Step = (typeof STEPS)[number];

/** 第一个还没满足的步骤（配置 → 授权清单看过即算 → 凭证可选 → 完成） */
export function firstOpenStep(configExists: boolean): Step {
  return configExists ? "fda" : "config";
}

export function SetupPage() {
  const { text, language } = useI18n();
  const { setup, permissions, pageErrors } = useAppState();
  const [step, setStep] = useState<Step | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    void refreshSetup();
    void refreshSecrets();
    void refreshPermissions();
  }, []);

  useEffect(() => {
    if (setup && step === null) setStep(firstOpenStep(setup.config_exists));
  }, [setup, step]);

  const index = step ? STEPS.indexOf(step) : 0;
  const go = (delta: number) => setStep(STEPS[Math.min(STEPS.length - 1, Math.max(0, index + delta))]);

  async function copyConfig() {
    setBusy(true);
    setNote(null);
    try {
      const receipt = await postSetupStep("config-from-example");
      setSetup(receipt.setup);
      setNote(text(`已创建 ${receipt.path}——先用默认值跑起来，之后随时在设置里改。`, `Created ${receipt.path} — runs on defaults now; tune it in Settings any time.`));
    } catch (err) {
      setNote(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function finish() {
    setBusy(true);
    setNote(null);
    try {
      const receipt = await postSetupStep("complete");
      setSetup(receipt.setup);
      navigate(buildAppUrl(window.location.href, "board", null));
    } catch (err) {
      setNote(errorMessage(err));
      setBusy(false);
    }
  }

  const stepTitle = (s: Step) => s === "config" ? text("配置文件", "Config file")
    : s === "fda" ? text("后台进程的磁盘授权", "Disk access for background processes")
      : s === "credentials" ? text("可选：Slack / Gmail / Anthropic", "Optional: Slack / Gmail / Anthropic")
        : text("完成", "Done");

  return (
    <main className="settings-page setup-page">
      <div className="settings-page-head">
        <h2 className="settings-page-title">{text("初始设置", "First-run setup")}</h2>
        <span className="settings-helper">{text("四步，随时可以关掉；完成前每次打开看板都会回到这里。", "Four steps; close any time — the board returns here until you finish.")}</span>
      </div>
      <ol className="setup-steps" aria-label={text("步骤", "Steps")}>
        {STEPS.map((s, i) => (
          <li key={s} className={`setup-step${s === step ? " is-current" : ""}${i < index ? " is-done" : ""}`}>
            <button type="button" onClick={() => setStep(s)}>{i + 1}. {stepTitle(s)}</button>
          </li>
        ))}
      </ol>
      {pageErrors.setup && <p className="settings-error" role="alert">{pageErrors.setup}</p>}

      {step === "config" && setup && (
        <section className="settings-section">
          <h3 className="settings-section-title">{stepTitle("config")}</h3>
          <p className="settings-helper">{text(`数据目录：${setup.home}。config.yaml 是管线的配置文件；一开始直接用模板的默认值就能跑，之后所有旋钮都能在设置页改（写的是 settings_overrides.json，模板不动）。`, `Home: ${setup.home}. config.yaml is the pipeline's config; the template defaults are enough to start, every knob is adjustable later in Settings (which writes settings_overrides.json and leaves the file alone).`)}</p>
          {setup.config_exists
            ? <p className="settings-helper is-ok">{text("config.yaml 已存在 ✓", "config.yaml exists ✓")}</p>
            : setup.config_example_exists
              ? <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void copyConfig()}>{text("从 config.example.yaml 创建", "Create from config.example.yaml")}</button>
              : <p className="settings-warning">{text("config.example.yaml 也不在——这个目录不像一个完整的 checkout；请重新 git clone 后 bash install.sh。", "config.example.yaml is missing too — this does not look like a full checkout; git clone again and run bash install.sh.")}</p>}
        </section>
      )}

      {step === "fda" && (
        <section className="settings-section">
          <h3 className="settings-section-title">{stepTitle("fda")}</h3>
          {permissions ? (
            <>
              <p className={permissions.fda.needed ? "settings-warning" : "settings-helper"}>
                {permissions.fda.needed
                  ? text("数据目录在 macOS 保护的位置：下面每个可执行文件都要在「系统设置 → 隐私与安全性 → 完全磁盘访问」里加一次（+ → ⌘⇧G → 粘贴路径）。这是后台雷达 / 派工 / 自动部署能读到仓库的前提。", "The home is in a macOS-protected location: add each executable below under System Settings → Privacy & Security → Full Disk Access (+ → ⌘⇧G → paste the path). Radars, dispatch and auto-deploy cannot read the repo without it.")
                  : text("数据目录不在受保护的位置，这一步通常可以跳过。", "The home is not in a protected location; this step can usually be skipped.")}
              </p>
              <ul className="settings-list">
                {permissions.fda.executables.filter((e) => e.path).map((e) => (
                  <li key={e.role} className="settings-list-row">
                    <span className="settings-list-title">{e.role}</span>
                    <code className="perm-inline">{e.realpath ?? e.path}</code>
                    <p className="settings-list-desc">{pickText(e.note, language)}</p>
                  </li>
                ))}
              </ul>
              <a className="settings-link" href={buildAppUrl(window.location.href, "permissions", null).toString()} target="_blank" rel="noreferrer">{text("打开完整的权限体检页（含屏幕录制 / 麦克风 / 通知）", "Open the full Permissions checkup (incl. Screen Recording / Microphone / Notifications)")}</a>
            </>
          ) : <p className="settings-helper">{text("探测中…", "Probing…")}</p>}
        </section>
      )}

      {step === "credentials" && (
        <section className="settings-section">
          <h3 className="settings-section-title">{stepTitle("credentials")}</h3>
          <p className="settings-helper">{text("都可以先跳过：没有凭证的源就静默不跑（不报错）。以后在设置 → 来源开关 里随时补。", "All optional: a source without a credential simply stays silent (no errors). Add them later under Settings → Sources.")}</p>
          <SecretRow name="slack-user-token.txt" links={[{ label: text("接入指南", "Setup guide"), href: "https://github.com/Wan-ZL/zelin-ai-assistant/blob/main/docs/SLACK_SETUP.md" }]} />
          <SecretRow name="gmail-app-password.txt" links={[{ label: text("生成应用专用密码", "Create an app password"), href: "https://myaccount.google.com/apppasswords" }]} helper={text("Gmail 地址在设置 → 来源开关 里填。", "The Gmail address goes under Settings → Sources.")} />
          <SecretRow name="anthropic-api-key.txt" links={[{ label: text("控制台", "Console"), href: "https://console.anthropic.com/settings/keys" }]} />
        </section>
      )}

      {step === "done" && setup && (
        <section className="settings-section">
          <h3 className="settings-section-title">{stepTitle("done")}</h3>
          <ul className="settings-list">
            <li className="settings-list-row">{setup.config_exists ? "✓" : "○"} config.yaml</li>
            <li className="settings-list-row">{Object.values(setup.secrets).some(Boolean) ? "✓" : "○"} {text("至少一把凭证（可选）", "At least one credential (optional)")}</li>
            <li className="settings-list-row">{permissions && !permissions.fda.needed ? "✓" : "○"} {text("完全磁盘访问（受保护位置才需要；真相看 doctor）", "Full Disk Access (only for protected locations; doctor tells the truth)")}</li>
          </ul>
          <p className="settings-helper">{text("点完成后看板不再回到向导；设置 → 关于 里可以重跑。后台服务（actd / server）由 bash install.sh 装好并常驻，看板顶部横幅会在它们停摆时说话。", "After Finish the board stops returning here; re-run from Settings → About. The background services (actd / server) were installed by bash install.sh and stay resident; the banner at the top speaks up when they stall.")}</p>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void finish()}>{busy ? text("保存中…", "Saving…") : text("完成，打开看板", "Finish and open the board")}</button>
        </section>
      )}

      <div className="settings-actions">
        <button type="button" className="btn" disabled={index === 0} onClick={() => go(-1)}>{text("上一步", "Back")}</button>
        <button type="button" className="btn" disabled={index >= STEPS.length - 1} onClick={() => go(1)}>{text("下一步", "Next")}</button>
        <a className="settings-link" href={buildAppUrl(window.location.href, "board", null).toString()}>{text("先去看板（下次再来）", "Go to the board (come back later)")}</a>
      </div>
      {note && <p className="settings-helper" role="status">{note}</p>}
    </main>
  );
}
