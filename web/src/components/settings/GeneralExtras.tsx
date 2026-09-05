// 通用区里原生有、但不是 settings_overrides 键的几行（§54.4 / §68.5 / §68.6；Settings.swift generalGroup + footerRow）：
//   · 登录时启动（壳在场时；SMAppService 经桥，失败弹原生同款 alert——LaunchAtLoginRow，关于页共用）；
//   · 看板动画（UserDefaults boardAnimations → localStorage 同名键 + <html data-board-animations>，animations.css 降级）；
//   · 初始设置向导 → 「重新运行初始设置」（POST /api/setup/reset 删完成标记后整页去向导）；
//   · 权限体检 → ?page=permissions（原生 general 区的「权限体检」按钮开 Permissions 窗）；
//   · 页脚「高级选项…在 config.yaml 中」+「打开 config.yaml」（§68.1 追记；原生 Settings.openConfigYaml）：先问
//     GET /api/setup 的 config_exists，缺席 → 既有 POST /api/setup/config-from-example 复制模板（§68.5；409 = 已在，视同成功）
//     → 再 POST /api/reveal {target:"config"}（§68.4 词表 target；客户端不传路径）。server/files.py 的模板回落只留给
//     doctor 失败动作「显示文件」——这颗按钮永不把 config.example.yaml 亮给用户去改。
//   · 终端应用 在目录字段 terminal_app（§68.1 追记）。
import { useState } from "react";
import { ApiError, postRevealTarget, postSetupStep } from "../../api";
import { useI18n } from "../../i18n";
import { buildAppUrl, navigate } from "../../route";
import { getState, refreshSetup, setSetup } from "../../store";
import { LaunchAtLoginRow } from "./LaunchAtLoginRow";
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
  const [configNote, setConfigNote] = useState<{ ok: boolean; message: string } | null>(null);

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

  /** 原生 openConfigYaml：config.yaml 缺席先从 config.example.yaml 复制，再定位 config.yaml。 */
  async function openConfig() {
    setConfigNote(null);
    try {
      // 点击时刻的真相（原生 fileExists 同拍）；刷新失败沿用 store 里的上一份快照
      await refreshSetup();
      const setup = getState().setup;
      let created = false;
      if (setup && !setup.config_exists) {
        try {
          const receipt = await postSetupStep("config-from-example");
          setSetup(receipt.setup);
          created = true;
        } catch (err) {
          // 409 = 别处刚建好（向导 / install.sh）——已存在就是我们要的状态
          if (!(err instanceof ApiError && err.status === 409)) throw err;
        }
      }
      await postRevealTarget("config");
      if (created) setConfigNote({ ok: true, message: text("已从 config.example.yaml 创建 config.yaml", "Created config.yaml from config.example.yaml") });
    } catch (err) {
      setConfigNote({ ok: false, message: errorMessage(err) });
    }
  }

  return (
    <>
      <LaunchAtLoginRow id="launch-at-login" />
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
      <div className="settings-actions">
        <span className="settings-helper">{text("高级选项（轮询间隔、digest 时间、不录制的 App、单独指定 4 个 Obsidian 管线目录等）在 config.yaml 中", "Advanced options (poll intervals, digest schedule, ignored apps, per-directory Obsidian pipeline paths, …) live in config.yaml")}</span>
        <button type="button" className="btn" onClick={() => void openConfig()}>{text("打开 config.yaml", "Open config.yaml")}</button>
        {configNote && (
          <span className={configNote.ok ? "settings-helper is-ok" : "settings-warning"} role={configNote.ok ? "status" : "alert"}>{configNote.message}</span>
        )}
      </div>
    </>
  );
}
