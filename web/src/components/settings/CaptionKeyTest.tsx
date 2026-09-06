// 火山两把 key 的「检测」（原生 CredentialRowView 的 isVolcano 分支：Settings.swift:1945–1946 / 2099–2148；
// §68.2 追记）：server 没有这两把 key 的探针（verify → 400），真连一次服务器的检测住在壳里搬来的
// CaptionKeyProbe——桥 `probeCaptionKey {name, value?}` 起跑，结果由快照 `captions.key_probe` 推回；这里按
// verdict 组原生 applyCaptionVerdict 的六句（✅ / ❌ / ⚠️），并把 ok / bad 回给凭证行做状态章。
// 框里有字 → 探这个值（不落盘；与「保存」分离——保存只存本机，文案早就这么承诺）；框空 → 探已保存的；
// 都没有 → 壳 reject「nothing to test」→ 提示先粘贴（或保存）。浏览器 / 老壳（UNKNOWN_METHOD）不渲染这颗按钮。
import { useEffect, useRef, useState } from "react";
import { useI18n } from "../../i18n";
import { callShell, hasShellBridge, useShellState, type ShellKeyProbe } from "../../shellBridge";

type Text = (zh: string, en: string) => string;

/** 原生 applyCaptionVerdict 的六句；ok = true / false / null（null = 判决不指向 key 本身，状态章不动） */
export function captionVerdictNote(probe: ShellKeyProbe, text: Text): { ok: boolean | null; message: string } {
  switch (probe.verdict) {
    case "ok":
      return { ok: true, message: text("✅ 有效（连接成功）", "✅ Valid (connected)") };
    case "bad_key":
      return { ok: false, message: text(`❌ Key 无效或未开通（${probe.detail}）`, `❌ Key invalid or service not activated (${probe.detail})`) };
    case "resource_not_enabled":
      return { ok: false, message: text(`❌ 资源未开通（${probe.code}：${probe.message}）——去语音控制台开通流式语音识别`, `❌ Resource not activated (${probe.code}: ${probe.message}) — enable streaming ASR in the speech console`) };
    case "model_not_found":
      return { ok: null, message: text(`❌ 模型 ID 不存在或未开通（${probe.detail}）——检查「翻译模型」填的 ID`, `❌ Model ID not found or not opened (${probe.detail}) — check the translation-model field`) };
    case "service_error":
      return { ok: null, message: text(`❌ 服务返回错误 ${probe.code}${probe.message ? "：" + probe.message : ""}`, `❌ Service error ${probe.code}${probe.message ? ": " + probe.message : ""}`) };
    case "network":
      return { ok: null, message: text(`⚠️ 网络不通（${probe.detail}）——稍后再点「检测」`, `⚠️ Network unreachable (${probe.detail}) — click Test again later`) };
    default:
      return { ok: null, message: probe.verdict };
  }
}

export interface CaptionKeyTestProps {
  name: string;
  /** 密码框当前内容（非空 = 探它） */
  value: string;
  disabled?: boolean;
  /** 判决落地：true / false 进状态章（已验证 ✓ / 验证失败），null 不动 */
  onVerdict: (ok: boolean | null) => void;
}

export function CaptionKeyTest({ name, value, disabled = false, onVerdict }: CaptionKeyTestProps) {
  const { text } = useI18n();
  const shell = useShellState();
  const [testing, setTesting] = useState(false);
  const [note, setNote] = useState<{ ok: boolean | null; message: string } | null>(null);
  const [unsupported, setUnsupported] = useState(false);
  const probe = shell?.captions.key_probe ?? null;
  const seen = useRef<ShellKeyProbe | null>(null);

  // 壳推回本行的判决（state=done）→ 组句、收忙态；只认这一轮（每个对象只处理一次）
  useEffect(() => {
    if (!testing || !probe || probe.name !== name || probe.state !== "done" || seen.current === probe) return;
    seen.current = probe;
    const verdict = captionVerdictNote(probe, text);
    setNote(verdict);
    onVerdict(verdict.ok);
    setTesting(false);
  }, [probe, testing, name, text, onVerdict]);

  if (!hasShellBridge() || unsupported) return null;

  async function run() {
    setTesting(true);
    setNote({ ok: null, message: text("检测中（真连一次服务器）…", "Testing (one real server connection)…") });
    try {
      const trimmed = value.trim();
      await callShell("probeCaptionKey", trimmed ? { name, value: trimmed } : { name });
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      setTesting(false);
      if (reason.startsWith("UNKNOWN_METHOD")) {
        setUnsupported(true);   // 老壳：没有这颗按钮可用，整颗撤下
        setNote(null);
        return;
      }
      setNote({ ok: false, message: reason.includes("nothing to test")
        ? text("先粘贴（或保存）一个凭证再验证", "Paste (or save) a credential first")
        : reason });
    }
  }

  return (
    <>
      <button type="button" className="btn" disabled={disabled || testing} onClick={() => void run()}>
        {testing ? text("检测中…", "Testing…") : text("检测", "Test")}
      </button>
      {note && (
        <span className={note.ok === false ? "settings-warning" : note.ok ? "settings-helper is-ok" : "settings-helper"} role={note.ok === false ? "alert" : "status"}>
          {note.message}
        </span>
      )}
    </>
  );
}
