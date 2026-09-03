// Gmail 接入区（§48 / §68；原生 SettingsGmail.swift 的 web 版，标签逐字镜像）：「启用 Gmail 雷达」开关 →
// 「抓取方式」A · 应用专用密码（推荐）/ B · 自定义抓取命令（= gmail_fetch_command 非空；单选只决定显示哪个字段）→
// ① 应用专用密码页链接（打不开？先开两步验证）② 填 Gmail 地址（目录 string 字段，占位「例：you@gmail.com」）
// ③ Gmail 应用密码（SecretRow，保存即验证 = IMAP 只读登录一次）→ 「后台雷达」行（launchd agent 状态 / 重新安装）
// + 「运行状态（真实轮询结果）」+ 立即测试一轮（RadarAgentPanel，§48.7）+ 健康摘要（§48 投影）。
import { useEffect, useState } from "react";
import { useI18n } from "../../i18n";
import { refreshSecrets, useAppState } from "../../store";
import { CatalogSection } from "./CatalogSection";
import { RadarAgentPanel } from "./RadarAgentPanel";
import { SecretRow } from "./SecretRow";
import { HealthLine } from "./sourceHealth";

type FetchPath = "app_password" | "command";

export function GmailSection() {
  const { text } = useI18n();
  const { board, secrets, settingsCatalog } = useAppState();
  useEffect(() => {
    if (!secrets) void refreshSecrets();
  }, [secrets]);
  const section = settingsCatalog?.sections.find((s) => s.id === "gmail");
  const command = section?.fields.find((f) => f.key === "gmail_fetch_command")?.effective;
  const [path, setPath] = useState<FetchPath | null>(null);
  const effectivePath: FetchPath = path ?? (typeof command === "string" && command.trim() ? "command" : "app_password");
  const health = board?.radar_sources?.gmail;

  const picker = (
    <div className="settings-field is-enum">
      <div className="settings-field-head"><span className="settings-knob-label">{text("抓取方式", "Fetch path")}</span></div>
      <div className="settings-radio-row" role="radiogroup" aria-label={text("抓取方式", "Fetch path")}>
        <label className="settings-radio">
          <input type="radio" name="gmail-fetch-path" value="app_password" checked={effectivePath === "app_password"} onChange={() => setPath("app_password")} />
          {text("A · 应用专用密码（推荐）", "A · App password (recommended)")}
        </label>
        <label className="settings-radio">
          <input type="radio" name="gmail-fetch-path" value="command" checked={effectivePath === "command"} onChange={() => setPath("command")} />
          {text("B · 自定义抓取命令", "B · Custom fetch command")}
        </label>
      </div>
    </div>
  );

  const stepsA = (
    <>
      <p className="settings-helper"><strong>{text("① 生成应用专用密码", "① Create an app password")}</strong></p>
      <div className="settings-actions">
        <a className="btn" href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer">{text("打开 Google 应用专用密码页", "Open Google app passwords")}</a>
        <a className="settings-link" href="https://myaccount.google.com/signinoptions/two-step-verification" target="_blank" rel="noreferrer">{text("打不开？先开两步验证", "Page unavailable? Enable 2-Step first")}</a>
      </div>
      <p className="settings-helper"><strong>{text("② 填 Gmail 地址", "② Enter your Gmail address")}</strong></p>
    </>
  );

  const isA = effectivePath === "app_password";
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
