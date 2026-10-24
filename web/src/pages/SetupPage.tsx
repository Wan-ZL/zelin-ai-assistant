// 首次运行向导（§15 v0.14 初始设置向导 → §68.5 web 版；?page=setup[&step=<name>]）。原生 SetupWizard 六步的 web 版：
// 一屏一步，全部按检测结果预填，一路「下一步」就能得到一套能用的系统：
//   1 欢迎 + 界面语言（store.chooseLanguage：切 UI + 写 general.language override，D37 单一开关）+ config.yaml（从 config.example.yaml 复制，绝不覆盖）
//   2 接入 AI 引擎（GET /api/setup/engine：claude CLI + API key 梯子；没有 key = 粘贴 key 先验后存——§78 的 headless claude 只认 API key）
//   3 系统权限（屏幕录制 / 笔记库 Documents / 通知 三行，与权限体检页同组件）+ 后台进程的磁盘授权（FDA 清单）
//   4 屏幕记录（一次性同意块 / 实时状态行，同权限体检页）
//   5 笔记放在哪里（当前生效的笔记库根 / 普通 Markdown 文件夹；「下一步」时 diff-write obsidian_raw）
//   6 最后检查（健康行 + 每个红行一颗修复按钮 + 「登录时自动启动」默认勾选行）→ 「完成」写 state/setup_done.json；
//     再也不弹，关于页可重跑。完成同时先按勾选经桥 `setLaunchAtLogin` 注册登录项（§28 追记 D39；壳报正式安装才提供，失败原句
//     留在本步、不放行；首跑默认勾选、表过态后重跑预填壳真相——localStorage 一次性标记 launchAtLoginDefaultApplied，原生同名）。
// v2.0.0 退役（全文见 tag full）：可选的 Slack / Gmail 凭证步、telemetry 披露块与「完成」时的 consent 标记 / wizard_complete 事件、
// 末步的「立即生成一次」看板与后台服务（actd）行。
// 新机器 / 空环境：config.yaml 缺席或 Anthropic key 没有（且没写过完成标记）时，app 开在这里而不是空的会议纪要页
// （app.tsx 按 GET /api/setup 的 needed 判定跳转）。幂等：每步预填当前真值、跳过不清数据；中途关掉下次还会回来
// （标记只在最后一步写）。页脚 = 原生 footer：进度点 · 第 N / 6 步 · 上一步 / 下一步 / 完成。
// 原生窗口的三件小事（2026-09-05 追回，§68.5 追记）：
//   · Return = 下一步 / 完成（原生 .keyboardShortcut(.defaultAction)）——焦点不在输入框 / 按钮 / 链接上、无修饰键、
//     不在 IME 组字时才算；首开步还没定（GET /api/setup 未回）时惰性；Esc 不绑；
//   · 整个向导期间 2 s 轮询壳的 TCC 探针（原生 perms.startPolling），末步另起 2.5 s 探针（health 的 cron 探针 + permissions 的
//     doctor 行；引擎每 4 拍静默复检一次）；
//   · 「先跳过（下次再来）」= 原生关窗：本窗口会话内不再跳回向导（sessionStorage 标记），新开窗口 / 下次启动再问，
//     向导本身仍可从 关于 / 设置 → 通用「重新运行初始设置」进来。
import { useCallback, useEffect, useState } from "react";
import "../components/settings/settings.css";
import "../components/permissions/permissions.css";
import { postSetupStep } from "../api";
import { CapabilityRows } from "../components/permissions/CapabilityRows";
import { RecordingConsentSection } from "../components/permissions/RecordingConsentSection";
import { usePermissionPolling } from "../components/permissions/usePermissionPolling";
import { pickText } from "../components/settings/catalogText";
import { errorMessage } from "../components/settings/useToast";
import { EngineStep, useEngineDetector } from "../components/setup/EngineStep";
import { FinaleStep } from "../components/setup/FinaleStep";
import { applyLaunchAtLoginChoice, defaultLaunchAtLogin, LaunchAtLoginChoice, launchAtLoginOffer, markLaunchAtLoginDefaultApplied } from "../components/setup/LaunchAtLoginChoice";
import { applyVaultChoice, VaultStep, type VaultChoice } from "../components/setup/VaultStep";
import { useI18n } from "../i18n";
import { buildAppUrl, navigate } from "../route";
import { hasShellBridge, useShellState } from "../shellBridge";
import { chooseLanguage, refreshHealth, refreshPermissions, refreshRecaps, refreshSecrets, refreshSetup, setSetup, useAppState } from "../store";

export const STEPS = ["welcome", "engine", "permissions", "recording", "vault", "finale"] as const;
export type Step = (typeof STEPS)[number];

/** 末步管线探针的节拍（原生 PipelineProbeModel 的 Timer 2.5 s，SetupWizard.swift:361） */
export const FINALE_PROBE_MS = 2500;
/** 引擎复检的稀疏度：每 N 拍静默 `GET /api/setup/engine` 一次（它要 spawn `claude --version`，不值得每拍都跑） */
export const ENGINE_RECHECK_EVERY = 4;
/** sessionStorage：本窗口会话里用户点过「先跳过（下次再来）」——app.tsx 的向导跳转本会话不再发生 */
export const SETUP_SKIPPED_KEY = "zai.setupSkipped";

/** 第一个还没满足的步骤：config.yaml 还没有就停在第 1 步（它就在那里建），否则从引擎开始 */
export function firstOpenStep(configExists: boolean): Step {
  return configExists ? "engine" : "welcome";
}

/** ?step=<name> 深链（判卷与「去配置」回跳都走它）；不认识的名字当没有 */
export function stepFromSearch(search: string): Step | null {
  const raw = new URLSearchParams(search).get("step");
  return raw && (STEPS as readonly string[]).includes(raw) ? (raw as Step) : null;
}

/** 「先跳过（下次再来）」：落会话标记再让链接自己导航（原生关窗 = 这次不问）。sessionStorage 不可写就当没点过（宁多问） */
export function markSetupSkipped(): void {
  try {
    window.sessionStorage.setItem(SETUP_SKIPPED_KEY, "1");
  } catch {
    /* 隐私模式等：跳过——下次加载仍会跳回向导 */
  }
}

export function isSetupSkipped(): boolean {
  try {
    return window.sessionStorage.getItem(SETUP_SKIPPED_KEY) === "1";
  } catch {
    return false;
  }
}

/** 焦点落在这些元素上时 Enter 归它们自己（输入框提交 / 按钮激活 / 链接跳转），向导不抢 */
const RETURN_OWNERS = new Set(["INPUT", "TEXTAREA", "SELECT", "BUTTON", "A"]);

/** Return 是否推进向导（原生 Next / Done 的 `.keyboardShortcut(.defaultAction)` 是窗口级的，所以听在 document 上——
 *  焦点在 body 时 <main> 收不到键）：Enter、无修饰键、不在 IME 组字、没被更内层的处理器认领（`defaultPrevented`——
 *  录制步的「开启」若要接 Return，preventDefault 即可）、焦点不在 RETURN_OWNERS 上。 */
export function isWizardReturn(event: Pick<KeyboardEvent, "key" | "altKey" | "ctrlKey" | "metaKey" | "shiftKey" | "defaultPrevented" | "isComposing" | "target">): boolean {
  if (event.key !== "Enter" || event.defaultPrevented || event.isComposing) return false;
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return false;
  const tag = event.target instanceof Element ? event.target.tagName.toUpperCase() : "";
  return !RETURN_OWNERS.has(tag);
}

export function SetupPage() {
  const { text, language } = useI18n();
  const { setup, permissions, pageErrors } = useAppState();
  const [step, setStep] = useState<Step | null>(() => stepFromSearch(window.location.search));
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [vaultChoice, setVaultChoice] = useState<VaultChoice | null>(null);
  const [vaultError, setVaultError] = useState<string | null>(null);
  // 终章「登录时自动启动」（D39）：null = 用户没碰过复选框 → 默认值随壳快照派生（首跑勾选；表过态后 = 壳的当前真相，
  // defaultLaunchAtLogin）；是否真能动手由壳的 launch_at_login_available 决定（launchAtLoginOffer）
  const [launchAtLoginChoice, setLaunchAtLoginChoice] = useState<boolean | null>(null);
  const detector = useEngineDetector();
  const present = hasShellBridge();
  const shell = useShellState();
  const { detect } = detector;
  const launchAtLogin = launchAtLoginChoice ?? defaultLaunchAtLogin(shell);

  useEffect(() => {
    void refreshSetup();
    void refreshSecrets();
    void refreshPermissions();
    void refreshHealth();
  }, []);
  // 原生 SetupWizardController.show：开窗即 perms.startPolling（2 s），关窗才停——不只权限步，整个向导都在场
  usePermissionPolling(present);

  useEffect(() => {
    if (setup && step === null) setStep(firstOpenStep(setup.config_exists));
  }, [setup, step]);

  // 末步：原生 probe.startPolling（2.5 s）——定时任务活性（health 的 cron 探针）+ 定时任务磁盘权限（permissions 的 doctor 行）
  // 每拍拉一次；引擎每 ENGINE_RECHECK_EVERY 拍静默复检（用户装好 claude / 登录回来，红行自己变绿）。离开末步 / 卸载即停。
  useEffect(() => {
    if (step !== "finale") return undefined;
    let ticks = 0;
    const timer = window.setInterval(() => {
      ticks += 1;
      void refreshHealth();
      void refreshPermissions();
      if (ticks % ENGINE_RECHECK_EVERY === 0) void detect({ quiet: true });
    }, FINALE_PROBE_MS);
    return () => window.clearInterval(timer);
  }, [step, detect]);

  const index = step ? STEPS.indexOf(step) : 0;
  const currentRoot = permissions?.vault?.root ?? "";

  const setStepAndSync = useCallback((next: Step) => {
    setStep(next);
    setNote(null);
    if (next === "engine" || next === "finale") void detect();
    if (next === "finale") {
      void refreshHealth();
      void refreshPermissions();
    }
  }, [detect]);

  async function advance() {
    // 首开步还没定（GET /api/setup 未回、step 仍是 null）：Return / 「下一步」惰性——否则页面一加载就按 Enter 会从
    // 「第 1 步」的空壳直接跳到引擎步，等 setup 回来 config_exists=false 时建 config.yaml 的欢迎步已被抢跳过
    if (step === null) return;
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
    if (busy) return;
    setBusy(true);
    setNote(null);
    // D39：登录项先落地、再写完成标记——桥拒绝（SMAppService 报错）时原句留在本步、不放行，用户可取消勾选再点一次；
    // 壳不在场 / 不是正式安装 = 行禁用，这里什么都不做（diff-write：勾选与壳真相一致也不打桥）
    const offer = launchAtLoginOffer(present, shell, text);
    if (offer.available && shell) {
      const err = await applyLaunchAtLoginChoice(launchAtLogin, shell, text);
      if (err) {
        setNote(err);
        setBusy(false);
        return;
      }
      // 表过态（原生 launchAtLoginDefaultApplied）：之后重跑向导，行预填壳的真相而不是再默认勾选——关掉过的不会被重新注册
      markLaunchAtLoginDefaultApplied();
    }
    try {
      const receipt = await postSetupStep("complete");
      setSetup(receipt.setup);
      navigate(buildAppUrl(window.location.href, "recaps"));
      // D40 换页不重载：回到会议纪要时补拉一次（向导期间 cron 可能刚跑出第一份），不等下一条 SSE
      void refreshRecaps();
      void refreshHealth();
    } catch (err) {
      setNote(errorMessage(err));
      setBusy(false);
    }
  }

  // Return = 页脚的默认按钮（末步「完成」，其余「下一步」）；判据见 isWizardReturn。监听器每次渲染重挂——
  // advance / finish 闭包着当前 step / busy，不用 ref 绕；Esc 不绑（原生也没有）。
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isWizardReturn(event)) return;
      event.preventDefault();
      if (index < STEPS.length - 1) void advance();
      else void finish();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  });

  // 界面语言单选 = store.chooseLanguage（D37 §15：立刻生效 + PUT general.language；顶栏 / `/lang` / 设置区同一把开关——此前这里
  // 是唯一的双写点，现在它住 store）。「已按系统语言预选;随时可在 设置 里更改。」这句原生原话（parity id
  // control:setup_wizard:copy:preselected-from-your-system-language-changeable）自 D37 起重新为真：设置里改的就是界面的语言

  const titles: Record<Step, [zh: string, en: string, subZh: string, subEn: string]> = {
    welcome: ["欢迎使用 Zelin's AI Assistant", "Welcome to Zelin's AI Assistant",
      "一个小助手:它录下你的屏幕(可选音频),每 30 分钟把内容整理进你的笔记库,开完会自动出一份可以直接粘贴的会议纪要。录到的东西不会自动发给任何人。",
      "A small assistant: it records your screen (audio optional), files what it saw into your notes vault every 30 minutes, and writes a paste-ready recap after each meeting. Nothing it records is ever sent anywhere on its own."],
    engine: ["接入 AI 引擎", "Connect the AI engine",
      "会议纪要与笔记整理由 claude CLI 在后台完成,用的是一把 Anthropic API key。这里检测 CLI 与 key——已有就不用动。",
      "Meeting recaps and note processing run on the claude CLI in the background, with an Anthropic API key. This step checks for both — if they are there, nothing to do."],
    permissions: ["系统权限", "System permissions",
      "需要用户亲手点的只有这几个系统开关。状态实时刷新——授权完成会自动变绿。",
      "These system switches are the only things macOS requires you to click yourself. Statuses refresh live — rows turn green as you grant them."],
    recording: ["屏幕记录", "Screen recording",
      "这是助手的核心数据来源。先看清楚采集什么、去哪里、留多久,再决定。",
      "This is the assistant's core data source. See what is captured, where it goes and how long it stays — then decide."],
    // D51：Obsidian 登记过的库又列出来了（GET /api/setup/vaults）——副标题回到原生原句（SetupWizard.swift:886-887）
    vault: ["笔记放在哪里?", "Where should notes live?",
      "屏幕记录提炼出的笔记存在这里。检测到的 Obsidian vault 已列出——不用 Obsidian 也完全没问题。",
      "Distilled notes live here. Obsidian vaults found on this Mac are listed — not using Obsidian is perfectly fine."],
    finale: ["最后检查", "Final check",
      "逐项确认系统真的能跑起来。红色的行都带一个修复按钮——绿完为止。",
      "Confirming the system actually runs. Every red row has one fix button — go until it's all green."],
  };
  const [titleZh, titleEn, subZh, subEn] = titles[step ?? "welcome"];

  return (
    <main className="settings-page setup-page">
      <div className="settings-page-head">
        <h2 className="settings-page-title">{text("初始设置", "Setup")}</h2>
        <a className="settings-link" href={buildAppUrl(window.location.href, "recaps").toString()} onClick={markSetupSkipped}>{text("先跳过（下次再来）", "Skip for now (come back later)")}</a>
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
                <label className="settings-radio"><input type="radio" name="setup-language" value="zh" checked={language === "zh"} onChange={() => void chooseLanguage("zh")} />中文</label>
                <label className="settings-radio"><input type="radio" name="setup-language" value="en" checked={language === "en"} onChange={() => void chooseLanguage("en")} />English</label>
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
            <h4 className="setup-card-title">{text("后台进程的磁盘授权", "Disk access for background processes")}</h4>
            {permissions ? (
              <>
                <p className={permissions.fda.needed ? "settings-warning" : "settings-helper"}>
                  {permissions.fda.needed
                    ? text("数据目录在 macOS 保护的位置：下面每个可执行文件都要在「系统设置 → 隐私与安全性 → 完全磁盘访问」里加一次（+ → ⌘⇧G → 粘贴路径）。这是定时整理与会议纪要能读到仓库的前提。", "The home is in a macOS-protected location: add each executable below under System Settings → Privacy & Security → Full Disk Access (+ → ⌘⇧G → paste the path). Scheduled processing and meeting recaps cannot read the repo without it.")
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
                <a className="settings-link" href={buildAppUrl(window.location.href, "permissions").toString()} target="_blank" rel="noreferrer">{text("打开完整的权限体检页", "Open the full Permissions Checkup")}</a>
              </>
            ) : <p className="settings-helper">{text("探测中…", "Probing…")}</p>}
          </>
        )}

        {step === "recording" && <RecordingConsentSection />}

        {step === "vault" && <VaultStep choice={vaultChoice} onChoose={setVaultChoice} error={vaultError} />}

        {step === "finale" && (
          <>
            <FinaleStep engine={detector.engine} engineChecking={detector.checking} goEngine={() => setStepAndSync("engine")} />
            <LaunchAtLoginChoice checked={launchAtLogin} onChange={setLaunchAtLoginChoice} shell={shell} />
          </>
        )}

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
          ? <button type="button" className="btn btn-primary" disabled={step === null} onClick={() => void advance()}>{text("下一步", "Next")}</button>
          : <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void finish()}>{busy ? text("保存中…", "Saving…") : text("完成", "Done")}</button>}
      </div>
    </main>
  );
}
