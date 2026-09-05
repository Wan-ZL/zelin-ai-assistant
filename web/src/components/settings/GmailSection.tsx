// Gmail 接入区（§14bis / §48 / §68；原生 SettingsGmail.swift 的 web 版，标签逐字镜像）：「启用 Gmail 雷达」开关 →
// 「抓取方式」A · 应用专用密码（推荐）/ B · 自定义抓取命令——A/B 的真源 = 生效的 gmail_fetch_command（§14bis 非空即赢），
// 不是本地单选状态：选 A 而命令生效着 → PUT {gmail_fetch_command: ""} 停用它（原生 setUseCommand(false) 删 override）+
// 「已切回 A：…」；选 B 而命令空着 → 「填好下面的抓取命令并点「保存」即生效。」（命令字段这才渲染）；本会话切回过 A 的命令
// 文本留在内存里，再选 B 直接写回（原生「命令文本保留着，切回 B 随时恢复」）→ ① 生成应用专用密码（两步验证前提 +
// 两个链接 + Workspace「The setting you are looking for is not available for your account」提示）② 填 Gmail 地址（目录
// string 字段，server-owned 的 email 形状校验，不合格不放行保存）③ Gmail 应用密码（SecretRow，保存即验证 = IMAP 只读登录
// 一次）→ 「后台雷达」行（launchd agent 状态 / 重新安装）+ 「运行状态（真实轮询结果）」+ 立即测试一轮（RadarAgentPanel，
// §48.7）+ 健康摘要（§48 投影）。开关翻开时 server 同一笔写 features.gmail_radar（§48.1，store.saveSettingsSection）。
import { useEffect, useState } from "react";
import { useI18n } from "../../i18n";
import { refreshSecrets, saveSettingsSection, useAppState } from "../../store";
import type { SettingsSection } from "../../types";
import { CatalogSection } from "./CatalogSection";
import { RadarAgentPanel } from "./RadarAgentPanel";
import { SecretRow } from "./SecretRow";
import { HealthLine } from "./sourceHealth";
import { errorMessage, useToast } from "./useToast";

/** 生效的抓取命令（trim 后；缺席 / 非字串 = ""） */
export function effectiveFetchCommand(section: SettingsSection | undefined): string {
  const value = section?.fields.find((f) => f.key === "gmail_fetch_command")?.effective;
  return typeof value === "string" ? value.trim() : "";
}

export function GmailSection() {
  const { text } = useI18n();
  const { board, secrets, settingsCatalog } = useAppState();
  useEffect(() => {
    if (!secrets) void refreshSecrets();
  }, [secrets]);
  const section = settingsCatalog?.sections.find((s) => s.id === "gmail");
  const command = effectiveFetchCommand(section);
  // 本地只记一种领先于 server 的意图：「选了 B 但命令还空着」；命令一旦生效（或被清掉）都回到派生值
  const [wantsB, setWantsB] = useState(false);
  useEffect(() => {
    if (command) setWantsB(false);
  }, [command]);
  // 切回 A 时停用的命令文本（原生留在输入框里，web 留在内存里；再选 B 直接写回）
  const [kept, setKept] = useState("");
  const [isSwitching, setSwitching] = useState(false);
  const [note, setNote] = useToast();
  const isB = command !== "" || wantsB;
  const isA = !isB;
  const health = board?.radar_sources?.gmail;

  const saveFailed = (err: unknown) =>
    setNote({ kind: "error", prefix: text("保存设置失败: ", "Failed to save settings: "), message: errorMessage(err) });

  // A：停用生效的命令（IMAP 接管）。本就没有生效命令时只是收起 B 的空态——没有要停用的东西，不发请求
  async function chooseA() {
    setWantsB(false);
    if (!command) return;
    setSwitching(true);
    setNote(null);
    try {
      const saved = await saveSettingsSection("gmail", { gmail_fetch_command: "" });
      if (effectiveFetchCommand(saved)) {
        // override 清了、config.yaml 里却还写着命令（§14bis 非空即赢）：如实说，不假报「已切回 A」
        setNote({ kind: "error", message: text("抓取命令来自 config.yaml——要走 A 请删掉那里的 sources.gmail.fetch_command。", "The fetch command comes from config.yaml — remove sources.gmail.fetch_command there to use path A.") });
      } else {
        setKept(command);
        setNote({ kind: "ok", message: text("已切回 A：走应用专用密码通道（抓取命令已停用，命令文本保留着，切回 B 随时恢复）。", "Back on path A: the app-password channel (the fetch command is deactivated; its text is kept — switch back to B anytime).") });
      }
    } catch (err) {
      saveFailed(err);
    } finally {
      setSwitching(false);
    }
  }

  // B：有留着的命令就直接写回（原生 setUseCommand(true) → saveFetchCommand()）；没有就等用户填好按「保存」
  async function chooseB() {
    setNote(null);
    if (command) return;
    if (!kept) {
      setWantsB(true);
      return;
    }
    setSwitching(true);
    try {
      await saveSettingsSection("gmail", { gmail_fetch_command: kept });
      setKept("");
      setNote({ kind: "ok", message: text("已保存 ✓ 下一轮（≤5 分钟）起雷达改走这条命令抓邮件；跑没跑成看下面「运行状态」。", "Saved ✓ From the next round (≤5 min) the radar fetches mail via this command; see \"Run status\" below for the truth.") });
    } catch (err) {
      setWantsB(true);
      saveFailed(err);
    } finally {
      setSwitching(false);
    }
  }

  const picker = (
    <div className="settings-field is-enum">
      <div className="settings-field-head"><span className="settings-knob-label">{text("抓取方式", "Fetch path")}</span></div>
      <div className="settings-radio-row" role="radiogroup" aria-label={text("抓取方式", "Fetch path")}>
        <label className="settings-radio">
          <input type="radio" name="gmail-fetch-path" value="app_password" checked={isA} disabled={isSwitching} onChange={() => void chooseA()} />
          {text("A · 应用专用密码（推荐）", "A · App password (recommended)")}
        </label>
        <label className="settings-radio">
          <input type="radio" name="gmail-fetch-path" value="command" checked={isB} disabled={isSwitching} onChange={() => void chooseB()} />
          {text("B · 自定义抓取命令", "B · Custom fetch command")}
        </label>
      </div>
      <p className="settings-helper">{text("公司 Workspace 禁用了应用专用密码时走 B——雷达定时调你自己的命令去抓邮件，抓回来的分诊完全相同。", "Use B when a Workspace admin has disabled app passwords — the radar periodically runs your own command to fetch mail; triage downstream is identical.")}</p>
      {isB && !command && (
        <p className="settings-helper">{text("填好下面的抓取命令并点「保存」即生效。", "Fill in the fetch command below and click Save to activate.")}</p>
      )}
      {note && (
        <div className={`settings-toast is-${note.kind}`} role={note.kind === "error" ? "alert" : "status"}>
          {note.prefix ? <span>{note.prefix}</span> : null}<span>{note.message}</span>
        </div>
      )}
    </div>
  );

  // 原生 stepCard 第 ① 步：两步验证前提 + 两个链接 + Workspace 管理员禁用的提示（16 位密码的长度提示在凭证行）
  const stepsA = (
    <>
      <p className="settings-helper"><strong>{text("① 生成应用专用密码（一次性，~1 分钟）", "① Generate an app password (one-time, ~1 min)")}</strong></p>
      <p className="settings-helper">{text("要求账号已开两步验证。页面里 App name 随便填（如 Zelin AI Assistant）→ 创建 → Google 显示 16 位密码（只显示这一次），复制它。", "Requires 2-Step Verification on the account. On the page, any app name works (e.g. Zelin AI Assistant) → Create → Google shows a 16-letter password (only once) — copy it.")}</p>
      <div className="settings-actions">
        <a className="btn" href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer">{text("打开 Google 应用专用密码页", "Open Google app passwords")}</a>
        <a className="settings-link" href="https://myaccount.google.com/signinoptions/two-step-verification" target="_blank" rel="noreferrer">{text("打不开？先开两步验证", "Page unavailable? Enable 2-Step first")}</a>
      </div>
      <p className="settings-helper">{text("公司 Google Workspace：页面若显示 “The setting you are looking for is not available for your account”，是管理员禁用了应用专用密码——此路不通，不用再试；你读邮件的画面仍会经屏幕录制链进入系统。", "Company Google Workspace: if the page says \"The setting you are looking for is not available for your account\", the admin has disabled app passwords — this path is closed, don't keep trying; mail you read on screen still reaches the system via the recording pipeline.")}</p>
      <p className="settings-helper"><strong>{text("② 填 Gmail 地址", "② Enter your Gmail address")}</strong></p>
    </>
  );

  const step3 = isA ? (
    <>
      <p className="settings-helper"><strong>{text("③ 粘贴应用密码（保存即验证）", "③ Paste the app password (verified on save)")}</strong></p>
      <SecretRow
        name="gmail-app-password.txt"
        labelOverride={text("Gmail 应用密码", "Gmail app password")}
        helper={text("16 位应用专用密码；验证 = IMAP 只读登录一次（需先填上方地址）。", "16-char app password; verify = one read-only IMAP login (fill in the address above first).")}
      />
    </>
  ) : null;

  return (
    <CatalogSection
      sectionId="gmail"
      only={isA ? ["gmail_enabled", "gmail_address"] : ["gmail_enabled", "gmail_address", "gmail_fetch_command"]}
      between={{ gmail_enabled: <>{picker}{isA ? stepsA : null}</>, gmail_address: step3 }}
    >
      <RadarAgentPanel source="gmail" />
      {health && <ul className="settings-health"><HealthLine source="gmail" health={health} /></ul>}
    </CatalogSection>
  );
}
