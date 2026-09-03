// 首次运行向导（§15 v0.14 初始设置向导 → §68.5 web 版；?page=setup[&step=<name>]）。原生 SetupWizard 六步的 web 版，
// 外加 web 独有的两件事（config.yaml 从模板建、可选的 Slack / Gmail 凭证）——一共七步，一屏一步，全部按检测结果
// 预填，一路「下一步」就能得到一套能用的系统：
//   1 欢迎 + 界面语言（写 general.language override）+ config.yaml（从 config.example.yaml 复制，绝不覆盖）
//   2 接入 AI 引擎（GET /api/setup/engine：claude CLI + 认证梯子；B 路：粘贴 key 先验后存）
//   3 系统权限（屏幕录制 / 笔记库 Documents / 通知 三行 + telemetry 披露块，与权限体检页同组件）+ 后台进程的磁盘授权（FDA 清单）
//   4 屏幕记录（一次性同意块 / 实时状态行，同权限体检页）
//   5 笔记放在哪里（当前生效的笔记库根 / 普通 Markdown 文件夹；「下一步」时 diff-write obsidian_raw）
//   6 可选：Slack / Gmail 凭证（SecretRow 经 server 写 0600）
//   7 最后检查（六行健康 + 每个红行一颗修复按钮）→ 「完成」写 state/setup_done.json；再也不弹，设置 → 关于 可重跑。
// 新机器 / 空环境：config.yaml 缺席或三把主凭证一把都没有（且没写过完成标记）时，看板开在这里而不是空看板
// （app.tsx 按 GET /api/setup 的 needed 判定跳转）。幂等：每步预填当前真值、跳过不清数据；中途关掉下次还会回来
// （标记只在最后一步写）。页脚 = 原生 footer：进度点 · 第 N / 7 步 · 上一步 / 下一步 / 完成。
import { useCallback, useEffect, useState } from "react";
import "../components/chrome/chrome.css";
import "../components/settings/settings.css";
import "../components/permissions/permissions.css";
import { postSetupStep } from "../api";
import { CapabilityRows } from "../components/permissions/CapabilityRows";
import { RecordingConsentSection } from "../components/permissions/RecordingConsentSection";
import { TelemetryBlock } from "../components/permissions/TelemetryBlock";
import { pickText } from "../components/settings/catalogText";
import { SecretRow } from "../components/settings/SecretRow";
import { errorMessage } from "../components/settings/useToast";
import { EngineStep, useEngineDetector } from "../components/setup/EngineStep";
import { FinaleStep } from "../components/setup/FinaleStep";
import { applyVaultChoice, VaultStep, type VaultChoice } from "../components/setup/VaultStep";
import { useI18n, type Language } from "../i18n";
import { buildAppUrl, navigate } from "../route";
import { callShell, hasShellBridge } from "../shellBridge";
import { refreshHealth, refreshPermissions, refreshSecrets, refreshSetup, saveSettingsSection, setLanguage, setSetup, useAppState } from "../store";

export const STEPS = ["welcome", "engine", "permissions", "recording", "vault", "credentials", "finale"] as const;
export type Step = (typeof STEPS)[number];

/** 第一个还没满足的步骤：config.yaml 还没有就停在第 1 步（它就在那里建），否则从引擎开始 */
export function firstOpenStep(configExists: boolean): Step {
  return configExists ? "engine" : "welcome";
}

/** ?step=<name> 深链（判卷与「去配置」回跳都走它）；不认识的名字当没有 */
export function stepFromSearch(search: string): Step | null {
  const raw = new URLSearchParams(search).get("step");
  return raw && (STEPS as readonly string[]).includes(raw) ? (raw as Step) : null;
}

export function SetupPage() {
  const { text, language } = useI18n();
  const { setup, permissions, pageErrors } = useAppState();
  const [step, setStep] = useState<Step | null>(() => stepFromSearch(window.location.search));
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [vaultChoice, setVaultChoice] = useState<VaultChoice | null>(null);
  const [vaultError, setVaultError] = useState<string | null>(null);
  const detector = useEngineDetector();
  const present = hasShellBridge();

  useEffect(() => {
    void refreshSetup();
    void refreshSecrets();
    void refreshPermissions();
    void refreshHealth();
    if (present) void callShell("getPermissions").catch(() => undefined);
  }, [present]);

  useEffect(() => {
    if (setup && step === null) setStep(firstOpenStep(setup.config_exists));
  }, [setup, step]);

  const index = step ? STEPS.indexOf(step) : 0;
  const currentRoot = permissions?.vault?.root ?? "";

  const setStepAndSync = useCallback((next: Step) => {
    setStep(next);
    setNote(null);
    if (next === "engine" || next === "finale") void detector.detect();
    if (next === "finale") {
      void refreshHealth();
      void refreshPermissions();
      if (present) void callShell("getPermissions").catch(() => undefined);
    }
  }, [detector, present]);

  async function advance() {
    // 笔记库没落盘成功就不放行：末步不能在笔记会落到别处（或哪里都不落）时宣布 🎉（原生 applyVaultChoice）
    if (step === "vault") {
      const err = await applyVaultChoice(vaultChoice, currentRoot);
      setVaultError(err);
      if (err) return;
      void refreshPermissions(true);
    }
    setStepAndSync(STEPS[Math.min(STEPS.length - 1, index + 1)]);
  }

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

  /** 显式选语言：立刻生效（store，持久化 zai.lang）+ 写设置里同一把 language override（原生 setLanguage） */
  function chooseLanguage(lang: Language) {
    setLanguage(lang);
    void saveSettingsSection("general", { language: lang }).catch(() => undefined);
  }

  const titles: Record<Step, [zh: string, en: string, subZh: string, subEn: string]> = {
    welcome: ["欢迎使用 Zelin's AI Assistant", "Welcome to Zelin's AI Assistant",
      "你的个人 AI 秘书:它记录你的屏幕、从邮件和消息里发现别人拜托你的事,整理成提案卡片;你只需要批准和验收,其余交给 AI。",
      "Your personal AI secretary: it captures your screen, finds what people ask of you in mail and messages, and turns it into proposal cards; you approve and accept — the AI does the rest."],
    engine: ["接入 AI 引擎", "Connect the AI engine",
      "批准的卡片由 claude CLI 在后台执行。这里检测你已有的配置——多数人无需任何操作。",
      "Approved cards are executed by the claude CLI in the background. This step detects your existing setup — most people need to do nothing."],
    permissions: ["系统权限", "System permissions",
      "需要用户亲手点的只有这几个系统开关。状态实时刷新——授权完成会自动变绿。",
      "These system switches are the only things macOS requires you to click yourself. Statuses refresh live — rows turn green as you grant them."],
    recording: ["屏幕记录", "Screen recording",
      "这是助手的核心数据来源。先看清楚采集什么、去哪里、留多久,再决定。",
      "This is the assistant's core data source. See what is captured, where it goes and how long it stays — then decide."],
    vault: ["笔记放在哪里?", "Where should notes live?",
      "屏幕记录提炼出的笔记存在这里,雷达也从这里发现待办。列出的是当前生效的笔记库——不用 Obsidian 也完全没问题。",
      "Distilled notes live here, and the radar scans it for asks. Listed is the vault in effect right now — not using Obsidian is perfectly fine."],
    credentials: ["可选：Slack / Gmail", "Optional: Slack / Gmail",
      "都可以先跳过：没有凭证的源就静默不跑（不报错）。以后在设置 → Slack / Gmail 接入 里随时补。",
      "All optional: a source without a credential simply stays silent (no errors). Add them later under Settings → Slack / Gmail."],
    finale: ["最后检查", "Final check",
      "逐项确认系统真的能跑起来。红色的行都带一个修复按钮——绿完为止。",
      "Confirming the system actually runs. Every red row has one fix button — go until it's all green."],
  };
  const [titleZh, titleEn, subZh, subEn] = titles[step ?? "welcome"];

  return (
    <main className="settings-page setup-page">
      <div className="settings-page-head">
        <h2 className="settings-page-title">{text("初始设置", "Setup")}</h2>
        <a className="settings-link" href={buildAppUrl(window.location.href, "board", null).toString()}>{text("先去看板（下次再来）", "Go to the board (come back later)")}</a>
      </div>
      {pageErrors.setup && <p className="settings-error" role="alert">{pageErrors.setup}</p>}

      <section className="settings-section" data-step={step ?? ""}>
        <h3 className="setup-title">{text(titleZh, titleEn)}</h3>
        <p className="setup-subtitle">{text(subZh, subEn)}</p>

        {step === "welcome" && (
          <>
            <div className="setup-card">
              <p className="settings-helper">{text("接下来几步(约 2 分钟)把系统配好。所有选项都已按检测结果预填——一路点「下一步」就能得到一套能用的系统,之后随时可在设置里修改。", "The next few steps (about 2 minutes) set everything up. Every option is prefilled from what was detected — clicking Next all the way through yields a working system, and everything stays changeable in Settings.")}</p>
            </div>
            <div className="setup-card">
              <h4 className="setup-card-title">{text("界面语言", "Interface language")}</h4>
              <div className="settings-radio-row" role="radiogroup" aria-label={text("界面语言", "Interface language")}>
                <label className="settings-radio"><input type="radio" name="setup-language" value="zh" checked={language === "zh"} onChange={() => chooseLanguage("zh")} />中文</label>
                <label className="settings-radio"><input type="radio" name="setup-language" value="en" checked={language === "en"} onChange={() => chooseLanguage("en")} />English</label>
              </div>
              <p className="settings-helper">{text("已按系统语言预选;随时可在 设置 里更改。", "Preselected from your system language; changeable anytime in Settings.")}</p>
            </div>
            {setup && (
              <div className="setup-card">
                <h4 className="setup-card-title">{text("配置文件", "Config file")}</h4>
                <p className="settings-helper">{text(`数据目录：${setup.home}。config.yaml 是管线的配置文件；一开始直接用模板的默认值就能跑，之后所有旋钮都能在设置页改（写的是 settings_overrides.json，模板不动）。`, `Home: ${setup.home}. config.yaml is the pipeline's config; the template defaults are enough to start, every knob is adjustable later in Settings (which writes settings_overrides.json and leaves the file alone).`)}</p>
                {setup.config_exists
                  ? <p className="settings-helper is-ok">{text("config.yaml 已存在 ✓", "config.yaml exists ✓")}</p>
                  : setup.config_example_exists
                    ? <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void copyConfig()}>{text("从 config.example.yaml 创建", "Create from config.example.yaml")}</button>
                    : <p className="settings-warning">{text("config.example.yaml 也不在——这个目录不像一个完整的 checkout；请重新 git clone 后 bash install.sh。", "config.example.yaml is missing too — this does not look like a full checkout; git clone again and run bash install.sh.")}</p>}
              </div>
            )}
          </>
        )}

        {step === "engine" && <EngineStep engine={detector.engine} checking={detector.checking} error={detector.error} detect={detector.detect} />}

        {step === "permissions" && (
          <>
            <CapabilityRows onError={setNote} />
            <TelemetryBlock />
            <h4 className="setup-card-title">{text("后台进程的磁盘授权", "Disk access for background processes")}</h4>
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
                <a className="settings-link" href={buildAppUrl(window.location.href, "permissions", null).toString()} target="_blank" rel="noreferrer">{text("打开完整的权限体检页", "Open the full Permissions Checkup")}</a>
              </>
            ) : <p className="settings-helper">{text("探测中…", "Probing…")}</p>}
          </>
        )}

        {step === "recording" && <RecordingConsentSection />}

        {step === "vault" && <VaultStep choice={vaultChoice} onChoose={setVaultChoice} error={vaultError} />}

        {step === "credentials" && (
          <>
            <SecretRow name="slack-user-token.txt" links={[{ label: text("接入指南", "Setup guide"), href: "https://github.com/Wan-ZL/zelin-ai-assistant/blob/main/docs/SLACK_SETUP.md" }]} />
            <SecretRow name="gmail-app-password.txt" links={[{ label: text("生成应用专用密码", "Create an app password"), href: "https://myaccount.google.com/apppasswords" }]} helper={text("Gmail 地址在设置 → Gmail 接入 里填。", "The Gmail address goes under Settings → Gmail.")} />
          </>
        )}

        {step === "finale" && <FinaleStep engine={detector.engine} engineChecking={detector.checking} goEngine={() => setStepAndSync("engine")} />}

        {note && <p className="settings-helper" role="status">{note}</p>}
      </section>

      <div className="setup-footer">
        <span className="setup-dots" aria-hidden="true">
          {STEPS.map((s, i) => <span key={s} className={`setup-dot${i <= index ? " is-reached" : ""}`} />)}
        </span>
        <span className="setup-progress">{text(`第 ${index + 1} / ${STEPS.length} 步`, `Step ${index + 1} of ${STEPS.length}`)}</span>
        <span className="setup-footer-spacer" />
        {index > 0 && <button type="button" className="btn" onClick={() => setStepAndSync(STEPS[index - 1])}>{text("上一步", "Back")}</button>}
        {index < STEPS.length - 1
          ? <button type="button" className="btn btn-primary" onClick={() => void advance()}>{text("下一步", "Next")}</button>
          : <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void finish()}>{busy ? text("保存中…", "Saving…") : text("完成", "Done")}</button>}
      </div>
    </main>
  );
}
