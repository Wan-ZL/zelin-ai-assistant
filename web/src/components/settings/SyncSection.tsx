// 设置 · 同步 / 配对（原生 SettingsSync.swift 的 web 版，文案逐字镜像；CONTRACT §31 / §35 / §68.15）：
// 开关 = 开 → POST /api/sync/pair（server 起 act.syncd --pair --json；幂等，同一 channel 同一码）/ 关 → POST /api/sync/disable
// （mode=off，密钥保留、重开不用重配对）；开着时 qrCard：二维码（syncd 用 act/lib/qr 画的 PNG，server base64 带回；
// 原生用 CoreImage 自画）+ 「设备名称:」+ 保存（改名 = 再跑一次 pair 带 --label；只在与已存名字不同且非空时亮）+ 主密钥
// 警句 + 「重新生成」（不带 label：syncd 沿用 state/sync.json 的名字，半截输入永不落盘）+ channel id。
// 状态句 / 错误句照原生：正在开启同步并生成配对二维码… / 正在重新生成配对二维码… / 正在更新设备名并刷新二维码… /
// 正在关闭同步… / 已开启 ✓ … / 已开启（频道注册会在联网后自动重试）… / 设备名已更新 ✓ … / 已关闭。… / 配对失败—— /
// 找不到可用的 python—— / 关闭失败——。syncd 是 state/sync 的唯一写者；server 只读它、只起它（§31）。
import { useEffect, useState } from "react";
import { fetchSync, postSyncDisable, postSyncPair } from "../../api";
import { useI18n } from "../../i18n";
import type { SyncPairReceipt, SyncStatus } from "../../types";
import { errorMessage } from "./useToast";

type Mode = "enable" | "repair" | "rename" | "disable";

export function SyncSection() {
  const { text } = useI18n();
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState<Mode | null>(null);
  const [statusNote, setStatusNote] = useState("");
  const [errorNote, setErrorNote] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetchSync().then((s) => {
      if (cancelled) return;
      setStatus(s);
      // 原生 loadIfNeeded：有存过的名字就预填它（重开不清自定义名），从未命名才用电脑名
      setLabel(s.label || s.default_label);
    }).catch((err) => { if (!cancelled) setErrorNote(errorMessage(err)); });
    return () => { cancelled = true; };
  }, []);

  const savedLabel = status?.label ?? "";
  const trimmed = label.trim();
  const labelDirty = trimmed !== "" && trimmed !== savedLabel;

  function applyPair(mode: Mode, receipt: SyncPairReceipt) {
    if (!receipt.ok) {
      setStatusNote("");
      setErrorNote(receipt.error === "no_python"
        ? text("找不到可用的 python——先在「通用 · 权限体检 / 初始设置向导」里装好运行环境。", "No usable python — set up the runtime first in General · Setup wizard.")
        : text("配对失败——请检查网络后重试（或看 state/syncd.log）。", "Pairing failed — check your network and retry (see state/syncd.log)."));
      return;
    }
    setStatus((prev) => ({
      enabled: true, channel_id: receipt.channel_id ?? "", label: receipt.label ?? "",
      default_label: prev?.default_label ?? "", qr_png_base64: receipt.qr_png_base64 ?? null,
    }));
    setLabel(receipt.label ?? "");
    setErrorNote("");
    if (mode === "rename") setStatusNote(text("设备名已更新 ✓ 二维码已同步刷新。", "Device name updated ✓ The QR has been refreshed too."));
    else if (receipt.registered) setStatusNote(text("已开启 ✓ 用手机扫下面的码即可配对。", "On ✓ Scan the code below from your phone to pair."));
    else setStatusNote(text("已开启（频道注册会在联网后自动重试）——二维码现在就能扫。", "On (channel registration retries automatically once online) — the QR is ready to scan now."));
  }

  async function pair(mode: Mode, explicitLabel?: string) {
    setBusy(mode);
    setErrorNote("");
    setStatusNote(mode === "enable" ? text("正在开启同步并生成配对二维码…", "Turning on sync and generating the pairing QR…")
      : mode === "repair" ? text("正在重新生成配对二维码…", "Regenerating the pairing QR…")
        : text("正在更新设备名并刷新二维码…", "Updating the device name and refreshing the QR…"));
    try {
      applyPair(mode, await postSyncPair(explicitLabel));
    } catch (err) {
      setStatusNote("");
      setErrorNote(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function disable() {
    setBusy("disable");
    setErrorNote("");
    setStatusNote(text("正在关闭同步…", "Turning off sync…"));
    try {
      const receipt = await postSyncDisable();
      if (!receipt.ok) {
        setStatusNote("");
        setErrorNote(text("关闭失败——请稍后重试。", "Couldn't turn it off — try again later."));
        return;
      }
      setStatus(receipt);
      setLabel(receipt.label || receipt.default_label);   // 丢掉半截输入：重开时不会把它存进去
      setStatusNote(text("已关闭。密钥保留在本机,随时可以再打开——不用重新配对。", "Off. The keys stay on this Mac; re-enable anytime — no re-pairing needed."));
    } catch (err) {
      setStatusNote("");
      setErrorNote(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  /** 原生 setEnabled：首次配对（没存过名字）才把预填的电脑名带上；有存过的名字就不传，syncd 沿用 */
  function toggle(on: boolean) {
    if (!on) return void disable();
    void pair("enable", !savedLabel && trimmed ? trimmed : undefined);
  }

  const enabled = status?.enabled ?? false;
  return (
    <section className="settings-section" id="settings-sync" aria-labelledby="settings-sync-title">
      <h3 id="settings-sync-title" className="settings-section-title">{text("同步 / 配对", "Sync / Pairing")}</h3>
      <p className="settings-helper">{text("把这台 Mac 的看板同步到手机,就能在手机上查看、远程审批。开启后生成一个配对二维码——在手机 App 里扫一次即可。卡片正文端到端加密,服务器和维护者都读不到明文。此区改动即时生效。", "Sync this Mac's board to your phone so you can view it and approve remotely. Turning it on generates a pairing QR — scan it once in the phone app. Card bodies are end-to-end encrypted; neither the server nor the maintainer can read them. Changes apply immediately.")}</p>
      <div className="settings-field is-bool">
        <div className="settings-field-head">
          <label className="settings-knob-label" htmlFor="sync-enabled">{text("开启同步 / 配对", "Enable sync / pairing")}</label>
        </div>
        <div className="settings-knob-controls">
          <input id="sync-enabled" type="checkbox" role="switch" className="settings-switch" checked={enabled} disabled={busy !== null || status === null} aria-checked={enabled} onChange={(e) => toggle(e.target.checked)} />
        </div>
      </div>
      {statusNote && <p className="settings-helper" role="status">{statusNote}</p>}
      {errorNote && <p className="settings-warning" role="alert">{errorNote}</p>}
      {enabled && status && (
        <div className="sync-qr-card">
          {status.qr_png_base64 ? (
            <>
              <img className="sync-qr" src={`data:image/png;base64,${status.qr_png_base64}`} width={220} height={220} alt={text("配对二维码", "Pairing QR code")} />
              <p className="settings-helper sync-qr-hint">{text("在手机 App 里扫这个码配对（每台 Mac 扫一次）", "Scan this in the phone app to pair (once per Mac).")}</p>
            </>
          ) : (
            <p className="settings-helper">{busy ? "…" : text("二维码还没生成——点「重新生成」。", "No QR yet — click Re-pair.")}</p>
          )}
          <div className="settings-knob-controls">
            <span className="settings-helper">{text("设备名称:", "Device name:")}</span>
            <input type="text" className="settings-input" aria-label={text("设备名称:", "Device name:")} placeholder={status.default_label} maxLength={64} value={label} disabled={busy !== null}
              onChange={(e) => setLabel(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && labelDirty) void pair("rename", trimmed); }} />
            <button type="button" className="btn" disabled={busy !== null || !labelDirty} onClick={() => void pair("rename", trimmed)}>{text("保存", "Save")}</button>
          </div>
          <p className="settings-helper">{text("这个名字会显示在手机 App 里。改名立即进二维码;已配对的手机在下一次刷新看板时自动更新名字,不用重新扫码。", "This name shows in the phone app. A rename goes into the QR immediately; already-paired phones pick up the new name on their next board refresh — no re-scan needed.")}</p>
          <p className="settings-helper">{text("⚠️ 这个二维码就是主密钥——谁扫到就能看你的看板、还能替你操作。别截图群发、别贴到公开的地方。", "⚠️ This QR is the master key — anyone who scans it can read your board and act on your behalf. Don't share screenshots or post it anywhere public.")}</p>
          <div className="settings-actions">
            <button type="button" className="btn" disabled={busy !== null} onClick={() => void pair("repair")}>{text("重新生成", "Re-pair")}</button>
            {status.channel_id && <code className="settings-global-path">{status.channel_id}</code>}
          </div>
        </div>
      )}
    </section>
  );
}
