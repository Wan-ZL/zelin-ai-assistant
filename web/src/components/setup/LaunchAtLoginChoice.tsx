// 向导终章「登录时自动启动（录制才能在重启后恢复）」一行（决策 D39，CONTRACT §28 追记）：原生 Onboarding.swift
// `registerLaunchAtLoginDefault` 在首次录制同意时**静默**把 app 注册成登录项（只在 /Applications 安装、一次性标记）；
// web 移植丢了这一默认，screenpipe 是壳的子进程 → 重启后没人拉起壳、录制不恢复、§28 的「壳登录自启在跑即常态」
// 成了空话。这里改成**显式、默认勾选**的一行：勾着时点「完成」= 桥 `setLaunchAtLogin {on:true}`——与 设置 → 关于
// 「登录时启动」（LaunchAtLoginRow）同一条桥方法、同一个 SMAppService 落点，不另起机制；之后那把开关是唯一真相。
// 只在壳报 `launch_at_login_available`（正式安装在 /Applications 或 ~/Applications、有 bundle id）时提供；浏览器
// （无桥）/ 开发版：行禁用 + 说明原因。D38 保证登录项启动是静默的（不弹窗、不抢焦点），所以默认勾选不扰人。
import { useI18n } from "../../i18n";
import { callShell, hasShellBridge, type ShellState } from "../../shellBridge";
import { launchAtLoginAlertTitle } from "../settings/LaunchAtLoginRow";

type Text = (zh: string, en: string) => string;

export interface LaunchAtLoginOffer {
  /** 能提供且能动手：壳在场 ∧ 快照到了 ∧ 壳说自己是正式安装 */
  available: boolean;
  /** 不能提供时的原因句（可 null：available 为 true） */
  reason: string | null;
}

/** 行能不能提供：浏览器（无桥）→ 不能（登录项是壳注册的）；壳在场但快照没到 → 先不能（等 getState）；
 *  壳说不是正式安装（开发版 / bare binary）→ 不能，说明原因；其余 → 能。 */
export function launchAtLoginOffer(shellPresent: boolean, shell: ShellState | null, text: Text): LaunchAtLoginOffer {
  if (!shellPresent) {
    return { available: false, reason: text("在浏览器里开不了：登录项由 Zelin's AI Assistant.app 自己注册——装好后在 app 里的 设置 → 关于 打开「登录时启动」。", "Not available in a browser: the login item is registered by Zelin's AI Assistant.app itself — turn on “Launch at login” under Settings → About inside the app.") };
  }
  if (!shell) {
    return { available: false, reason: text("正在读取 app 状态…", "Reading the app state…") };
  }
  if (!shell.launch_at_login_available) {
    return { available: false, reason: text("这个 app 不是装在 /Applications（或 ~/Applications）里的正式版——开发版不注册登录项（会钉住临时路径）。正式安装后在 设置 → 关于 打开。", "This app is not the installed copy under /Applications (or ~/Applications) — a dev build never registers a login item (it would pin a temporary path). Turn it on under Settings → About after installing.") };
  }
  return { available: true, reason: null };
}

/** 「完成」时的落地：diff-write——行的勾选与壳的真相一致就不打桥；否则同一条 `setLaunchAtLogin {on}`。
 *  回 null = 成功 / 无事可做；回字串 = 原生 loginItemAlert 同款「标题: 壳原句」（调用方展示、不放行「完成」，
 *  用户可取消勾选再点一次——失败不许静默吞掉，也不许把向导卡死）。 */
export async function applyLaunchAtLoginChoice(checked: boolean, shell: ShellState, text: Text): Promise<string | null> {
  if (shell.launch_at_login === checked) return null;
  try {
    await callShell("setLaunchAtLogin", { on: checked });
    return null;
  } catch (err) {
    const raw = err instanceof Error ? err.message : String(err);
    const reason = raw.replace(/^INVALID_ARGS:\s*launch at login:\s*/i, "");
    return `${launchAtLoginAlertTitle(checked, reason, text)}: ${reason}`;
  }
}

export interface LaunchAtLoginChoiceProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  shell: ShellState | null;
}

export function LaunchAtLoginChoice({ checked, onChange, shell }: LaunchAtLoginChoiceProps) {
  const { text } = useI18n();
  const offer = launchAtLoginOffer(hasShellBridge(), shell, text);
  const id = "setup-launch-at-login";
  return (
    <div className="setup-card" data-testid="setup-launch-at-login">
      <label className="settings-check" htmlFor={id}>
        <input id={id} type="checkbox" checked={offer.available && checked} disabled={!offer.available} onChange={(e) => onChange(e.target.checked)} />
        <span>{text("登录时自动启动（录制才能在重启后恢复）", "Launch at login (recording only resumes after a reboot with this on)")}</span>
      </label>
      {offer.available
        ? <p className="settings-helper">{text("勾着时点「完成」会把 Zelin's AI Assistant 注册为登录项；登录时它在后台静默启动、不弹窗。之后随时在 设置 → 关于「登录时启动」改。", "With this checked, Done registers Zelin's AI Assistant as a login item; at login it starts silently in the background without opening a window. Change it any time under Settings → About → “Launch at login”.")}</p>
        : <p className="settings-helper" data-testid="setup-launch-at-login-reason">{offer.reason}</p>}
    </div>
  );
}
