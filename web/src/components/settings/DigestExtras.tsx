// 每周摘要区的「现在生成一份」（原生 SettingsWeeklyDigest.swift 的 Button + generateNow；CONTRACT §24 / §68.1 追记）：
// 点一下 → POST /api/actions {action:"weekly_digest_now"}（server/inbox_writer 词表早已收这个动词；actd 转成 detached
// `python -m act.weekly_digest --now`）。回执一句逐字镜像原生：成功「已请求生成——完成后会弹通知，摘要出现在「待验收」。」；
// 失败「没能写入请求（磁盘问题），请再点一次：」+ 原句（前缀独立节点，探针按节点直接文本判）。
// 开关本身是目录字段 weekly_digest_enabled（CatalogSection 渲，随「保存」落盘），状态字在 SettingsPage.DigestStatus。
import { useState } from "react";
import { postAction } from "../../api";
import { useI18n } from "../../i18n";
import { errorMessage } from "./useToast";

type Receipt = { ok: true } | { ok: false; detail: string };

export function DigestExtras() {
  const { text } = useI18n();
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<Receipt | null>(null);

  async function generateNow() {
    setBusy(true);
    setReceipt(null);
    try {
      await postAction({ action: "weekly_digest_now" });
      setReceipt({ ok: true });
    } catch (err) {
      setReceipt({ ok: false, detail: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings-actions digest-extras">
      <button type="button" className="btn" disabled={busy} onClick={() => void generateNow()}>{text("现在生成一份", "Generate now")}</button>
      {receipt?.ok === true && (
        <span className="settings-helper" role="status">
          {text("已请求生成——完成后会弹通知，摘要出现在「待验收」。", "Requested — you'll get a notification; the recap appears in the Review lane.")}
        </span>
      )}
      {receipt?.ok === false && (
        <span className="settings-warning" role="alert">
          <span>{text("没能写入请求（磁盘问题），请再点一次：", "Could not write the request (disk issue) — try again: ")}</span>
          <span>{receipt.detail}</span>
        </span>
      )}
    </div>
  );
}
