// 「登录时启动」一行（原生 Settings.swift generalGroup 的 Toggle + setLaunchAtLogin；§68.6 / §68.13）：壳在场时
// 经桥 `setLaunchAtLogin {on}`（SMAppService.mainApp）。失败 = 原生 loginItemAlert 的样子：一个弹窗，标题三选一
// ——开发版 / 非 app bundle 开不了（「无法开启登录时启动」，壳 reject 说 not an app bundle）、开失败（「开启登录时启动
// 失败」）、关失败（「关闭登录时启动失败」）——正文 = 壳的原句，一颗「好」。通用区与关于页共用这一行（原生只在通用区；
// web 的关于页也留一份是 §68.6 的落点）。浏览器里（无桥）整行不渲染。
import { useState } from "react";
import { useI18n } from "../../i18n";
import { callShell, hasShellBridge, useShellState } from "../../shellBridge";
import { ModalDialog } from "../board/ModalDialog";

type Text = (zh: string, en: string) => string;

/** 原生三种 alert 标题：壳 LaunchAtLogin.set 的拒绝句里说 not an app bundle = 原生「从 build 目录运行的开发版」那一支 */
export function launchAtLoginAlertTitle(on: boolean, reason: string, text: Text): string {
  if (!on) return text("关闭登录时启动失败", "Failed to disable launch at login");
  if (/not an app bundle|dev build/i.test(reason)) return text("无法开启登录时启动", "Can't enable launch at login");
  return text("开启登录时启动失败", "Failed to enable launch at login");
}

/** 桥 reject 的 `INVALID_ARGS: launch at login: <why>` → `<why>`（其它原样） */
function stripBridgePrefix(reason: string): string {
  return reason.replace(/^INVALID_ARGS:\s*launch at login:\s*/i, "");
}

export function LaunchAtLoginRow({ id = "launch-at-login", helper }: { id?: string; helper?: string }) {
  const { text } = useI18n();
  const shell = useShellState();
  const [busy, setBusy] = useState(false);
  const [alert, setAlert] = useState<{ title: string; body: string } | null>(null);
  if (!hasShellBridge() || !shell) return null;

  async function toggle(on: boolean) {
    setBusy(true);
    try {
      await callShell("setLaunchAtLogin", { on });
    } catch (err) {
      const reason = stripBridgePrefix(err instanceof Error ? err.message : String(err));
      setAlert({ title: launchAtLoginAlertTitle(on, reason, text), body: reason });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings-field is-bool">
      <div className="settings-field-head">
        <label className="settings-knob-label" htmlFor={id}>{text("登录时启动", "Launch at login")}</label>
      </div>
      <div className="settings-knob-controls">
        <input id={id} type="checkbox" role="switch" className="settings-switch" checked={shell.launch_at_login} disabled={busy} onChange={(e) => void toggle(e.target.checked)} />
        {helper && <span className="settings-helper">{helper}</span>}
      </div>
      {alert && (
        <ModalDialog title={alert.title} onCancel={() => setAlert(null)}>
          <p className="dialog-body">{alert.body}</p>
          <div className="dialog-actions">
            <button type="button" className="btn btn-primary" onClick={() => setAlert(null)}>{text("好", "OK")}</button>
          </div>
        </ModalDialog>
      )}
    </div>
  );
}
