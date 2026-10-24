// 通用区里原生有、但不是 settings_overrides 键的几行（§54.4 / §68.5 / §68.6；Settings.swift generalGroup + footerRow）：
//   · 登录时启动（壳在场时；SMAppService 经桥，失败弹原生同款 alert——LaunchAtLoginRow，关于页共用）；
//   · 初始设置向导 → 「重新运行初始设置」（POST /api/setup/reset 删完成标记后整页去向导）；
//   · 权限体检 → ?page=permissions（原生 general 区的「权限体检」按钮开 Permissions 窗）；
//   · 页脚「高级选项…在 config.yaml 中」+「打开 config.yaml」（§68.1 追记；原生 Settings.openConfigYaml）：先问
//     GET /api/setup 的 config_exists，缺席或不知道（快照为 null）→ 既有 POST /api/setup/config-from-example 复制模板
//     （§68.5；409 = 已在，视同成功）→ 再 POST /api/reveal {target:"config"}（§68.4 词表 target；客户端不传路径）。
//     server 的模板回落只留给 doctor 失败动作「显示文件」——这颗按钮永不把 config.example.yaml 亮给用户去改。
// v2.0.0 退役（全文见 tag full）：「看板动画」开关（看板随 Act 管线退役）与终端应用字段。
import { useState } from "react";
import { ApiError, postRevealTarget, postSetupStep } from "../../api";
import { useI18n } from "../../i18n";
import { buildAppUrl, navigate } from "../../route";
import { getState, refreshSetup, setSetup } from "../../store";
import { LaunchAtLoginRow } from "./LaunchAtLoginRow";
import { errorMessage } from "./useToast";

export function GeneralExtras() {
  const { text } = useI18n();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  // 「打开 config.yaml」的两条回执各自独立：本次由我们建出 config.yaml（status）/ 哪一步失败了（alert）——
  // 复制成功、reveal 失败时两句同时在，用户得知道文件已经建好了
  const [configCreated, setConfigCreated] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);

  async function rerunSetup() {
    setBusy(true);
    setNote(null);
    try {
      const receipt = await postSetupStep("reset");
      setSetup(receipt.setup);
      navigate(buildAppUrl(window.location.href, "setup"));
    } catch (err) {
      setNote(errorMessage(err));
      setBusy(false);
    }
  }

  /** 原生 openConfigYaml：config.yaml 缺席先从 config.example.yaml 复制，再定位 config.yaml。 */
  async function openConfig() {
    setConfigCreated(false);
    setConfigError(null);
    try {
      // 点击时刻的真相（原生 fileExists 同拍）；刷新失败沿用 store 里的上一份快照。
      // 连上一份也没有（首屏刷新也失败 / 还没回来）→ 视同缺席去复制：409 说明它在，其它错误说明不该 reveal——
      // 唯独不能因为「不知道」就直接 reveal，server/files.py 会回落到模板，正是这颗按钮要堵的路。
      await refreshSetup();
      const setup = getState().setup;
      if (!setup || !setup.config_exists) {
        try {
          const receipt = await postSetupStep("config-from-example");
          setSetup(receipt.setup);
          setConfigCreated(true);
        } catch (err) {
          // 409 = 别处刚建好（向导 / install.sh）——已存在就是我们要的状态
          if (!(err instanceof ApiError && err.status === 409)) throw err;
        }
      }
      await postRevealTarget("config");
    } catch (err) {
      setConfigError(errorMessage(err));
    }
  }

  return (
    <>
      <LaunchAtLoginRow id="launch-at-login" />
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
          <a className="btn" href={buildAppUrl(window.location.href, "permissions").toString()}>{text("打开权限体检", "Open Permissions checkup")}</a>
        </div>
      </div>
      <div className="settings-actions">
        <span className="settings-helper">{text("高级选项（会议纪要的出稿形状、单独指定 4 个 Obsidian 管线目录等）在 config.yaml 中", "Advanced options (the recap output shape, per-directory Obsidian pipeline paths, …) live in config.yaml")}</span>
        <button type="button" className="btn" onClick={() => void openConfig()}>{text("打开 config.yaml", "Open config.yaml")}</button>
        {configCreated && <span className="settings-helper is-ok" role="status">{text("已从 config.example.yaml 创建 config.yaml", "Created config.yaml from config.example.yaml")}</span>}
        {configError && <span className="settings-warning" role="alert">{configError}</span>}
      </div>
    </>
  );
}
