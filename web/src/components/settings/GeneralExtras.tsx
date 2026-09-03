// 通用区里原生有、但不是 settings_overrides 键的三行（§54.4 / §68.5；Settings.swift generalGroup）：
//   · 看板动画（UserDefaults boardAnimations → localStorage 同名键 + <html data-board-animations>，animations.css 降级）；
//   · 初始设置向导 → 「重新运行初始设置」（POST /api/setup/reset 删完成标记后整页去向导）；
//   · 权限体检 → ?page=permissions（原生 general 区的「权限体检」按钮开 Permissions 窗）；
//   · 终端应用 / 登录时启动 不在这里：web 只有 Terminal.app 一种（§68.14）、登录时启动住在关于页的壳区（§68.6）。
import { useState } from "react";
import { postSetupStep } from "../../api";
import { useI18n } from "../../i18n";
import { buildAppUrl, navigate } from "../../route";
import { setSetup } from "../../store";
import { errorMessage } from "./useToast";

const ANIMATIONS_KEY = "boardAnimations";

export function readBoardAnimations(): boolean {
  try {
    return window.localStorage.getItem(ANIMATIONS_KEY) !== "false";
  } catch {
    return true;
  }
}

/** 落地到 <html>：off 才写属性（默认开 = 无属性，CSS 零成本） */
export function applyBoardAnimations(on: boolean) {
  if (on) delete document.documentElement.dataset.boardAnimations;
  else document.documentElement.dataset.boardAnimations = "off";
}

export function GeneralExtras() {
  const { text } = useI18n();
  const [animations, setAnimations] = useState<boolean>(readBoardAnimations);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  function toggleAnimations(on: boolean) {
    setAnimations(on);
    applyBoardAnimations(on);
    try {
      window.localStorage.setItem(ANIMATIONS_KEY, String(on));
    } catch {
      /* localStorage 不可写：本次会话仍生效 */
    }
  }

  async function rerunSetup() {
    setBusy(true);
    setNote(null);
    try {
      const receipt = await postSetupStep("reset");
      setSetup(receipt.setup);
      navigate(buildAppUrl(window.location.href, "setup", null));
    } catch (err) {
      setNote(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <>
      <div className="settings-field is-bool">
        <div className="settings-field-head">
          <label className="settings-knob-label" htmlFor="setting-general-boardAnimations">{text("看板动画", "Board animations")}</label>
        </div>
        <div className="settings-knob-controls">
          <input id="setting-general-boardAnimations" type="checkbox" role="switch" className="settings-switch" checked={animations} aria-checked={animations} onChange={(e) => toggleAnimations(e.target.checked)} />
        </div>
        <p className="settings-helper">{text("卡片移动 / 落定的过渡动效；关掉后即时生效，只影响这台设备的这个浏览器。", "Card move / settle transitions; takes effect immediately, on this device and browser only.")}</p>
      </div>
      <div className="settings-field is-string">
        <div className="settings-field-head"><span className="settings-knob-label">{text("初始设置向导", "Setup wizard")}</span></div>
        <div className="settings-knob-controls">
          <button type="button" className="btn" disabled={busy} onClick={() => void rerunSetup()}>{text("重新运行初始设置", "Re-run setup")}</button>
        </div>
        {note && <p className="settings-warning" role="alert">{note}</p>}
      </div>
      <div className="settings-field is-string">
        <div className="settings-field-head"><span className="settings-knob-label">{text("权限体检", "Permissions checkup")}</span></div>
        <div className="settings-knob-controls">
          <a className="btn" href={buildAppUrl(window.location.href, "permissions", null).toString()}>{text("打开权限体检", "Open Permissions checkup")}</a>
        </div>
      </div>
    </>
  );
}
