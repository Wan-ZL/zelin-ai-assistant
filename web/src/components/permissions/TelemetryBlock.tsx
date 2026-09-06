// 首次运行的 telemetry 披露块（原生 Permissions.swift TelemetryBlockView 的 web 版，§15 v0.48 opt-in）：
// 一句诚实的话（行为元数据默认开；输入文本只在下方勾选后才上传）+ 「详情与关闭在设置。」深链 + 一颗
// 「分享输入文本以帮助改进产品」复选框——写的就是设置目录 telemetry 区的 `telemetry.capture_input`
// （server diff-write 嵌套 override，与设置页开关同一键，§68.1），两处永不打架。权限体检页与向导第 3 步共用。
import { useEffect, useState } from "react";
import { useI18n } from "../../i18n";
import { buildAppUrl } from "../../route";
import { refreshSettingsCatalog, saveSettingsSection, useAppState } from "../../store";
import { errorMessage } from "../settings/useToast";

const KEY = "telemetry.capture_input";

export function TelemetryBlock() {
  const { text } = useI18n();
  const { settingsCatalog } = useAppState();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!settingsCatalog) void refreshSettingsCatalog();
  }, [settingsCatalog]);

  const field = settingsCatalog?.sections.find((s) => s.id === "telemetry")?.fields.find((f) => f.key === KEY);
  const checked = field?.effective === true;

  async function toggle(next: boolean) {
    setBusy(true);
    setError(null);
    try {
      await saveSettingsSection("telemetry", { [KEY]: next });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const settingsUrl = buildAppUrl(window.location.href, "settings", null);
  settingsUrl.searchParams.set("anchor", "telemetry");

  return (
    <div className="settings-list-row perm-telemetry">
      <p className="settings-list-desc">
        {text("匿名使用统计（仅事件元数据，如事件名/耗时/计数）默认开启以改进产品；你输入的文本默认不上传，仅在下方勾选后收集（每条截断 500 字）。",
          "Anonymous usage stats (event metadata only — event names, timings, counts) are on by default to improve the product; the text you type is NOT uploaded by default and is collected only if you opt in below (clipped to 500 chars each).")}
        {" "}
        <a className="settings-link" href={settingsUrl.toString()}>{text("详情与关闭在设置。", "Details & opt-out in Settings.")}</a>
      </p>
      <label className="settings-checkbox">
        <input type="checkbox" checked={checked} disabled={busy || !field} onChange={(e) => void toggle(e.target.checked)} />
        {text("分享输入文本以帮助改进产品", "Share typed text to improve the product")}
      </label>
      {error && <p className="settings-warning" role="alert">{error}</p>}
    </div>
  );
}
