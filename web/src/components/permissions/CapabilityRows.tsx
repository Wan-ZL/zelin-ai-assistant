// 系统权限行（原生 Permissions.swift CapabilityRowsView 的 web 版，§68.3）：屏幕录制 / 笔记库访问（Documents）
// / 通知 三行（web 另加麦克风——实时字幕的转写要它），权限体检页与初始设置向导第 3 步共用同一组件。
// 真相只有壳知道（TCC 探针是原生 API，§61 桥 getPermissions 回报 granted / denied / unknown；笔记库另有
// server 的被动探针 state/vault_sync_mode=mirror 兜底）；每行 = 名字 + 状态词 + 为什么要它 + 一颗按钮
// （去授权… / 请求权限… / 打开系统设置），授了就只剩 ✓。文案逐字镜像 Permissions.swift:541–585。屏幕行的按钮在一次性系统提示
// 弹过之后改说「打开系统设置」（壳 `permissions.screen_requested`，§68.3 追记 / parity 批 `recording-consent-header-ui`）。
// 浏览器里打开（无桥）：如实说「只在看板 app 里可探」，不装假按钮。
import { useI18n } from "../../i18n";
import { callShell, hasShellBridge, useShellState, type PermissionStatus } from "../../shellBridge";
import { useAppState } from "../../store";

type Text = (zh: string, en: string) => string;
type Kind = "screen" | "vault" | "notifications" | "microphone";

/** 三态状态词（原生 statusText；屏幕行没有「尚未请求」——探针只答有 / 无） */
export function capabilityStatusText(kind: Kind, status: PermissionStatus, text: Text): string {
  if (status === "granted") return text("已授权", "Granted");
  if (status === "denied" || kind === "screen") return text("未授权", "Not granted");
  return text("尚未请求", "Not requested yet");
}

/** 按钮动词（原生 buttonLabel）：屏幕 = 去授权…，一次性系统提示弹过之后（壳 `screenPermissionRequested`，快照
 *  `permissions.screen_requested`，§61.1 追记）→ 打开系统设置——按钮说的与壳接下来做的（深链）一致，Permissions.swift:548-549；
 *  笔记库被拒后 → 打开系统设置，否则 去授权…；通知 / 麦克风 unknown → 请求权限…，被拒 → 打开系统设置 */
export function capabilityButtonLabel(kind: Kind, status: PermissionStatus, text: Text, screenRequested = false): string {
  if (kind === "notifications" || kind === "microphone") {
    return status === "denied" ? text("打开系统设置", "Open System Settings") : text("请求权限", "Request…");
  }
  if (kind === "vault" && status === "denied") return text("打开系统设置", "Open System Settings");
  if (kind === "screen" && screenRequested) return text("打开系统设置", "Open System Settings");
  return text("去授权", "Grant…");
}

const ORDER: Kind[] = ["screen", "vault", "notifications", "microphone"];

function rowName(kind: Kind, text: Text): string {
  switch (kind) {
    case "screen": return text("屏幕录制", "Screen Recording");
    case "vault": return text("笔记库访问（Documents）", "Notes vault access (Documents)");
    case "notifications": return text("通知", "Notifications");
    default: return text("麦克风", "Microphone");
  }
}

function rowWhy(kind: Kind, text: Text): string {
  switch (kind) {
    case "screen":
      return text("这是本产品的核心数据来源——没有这项授权,录制引擎启动后会立刻退出,记不到任何内容。",
        "This is the product's core data source — without it the capture engine exits instantly and nothing gets recorded.");
    case "vault":
      return text("授权一次，后台管线就永远经由 App 的稳定身份读写 Obsidian 笔记库——此后 claude/python 升级不会再弹任何权限窗口。",
        "Grant once and the background pipeline reaches your Obsidian vault through the app's stable identity forever — claude/python updates can never trigger new permission prompts again.");
    case "notifications":
      return text("有新提案卡、任务完成或需要你输入时,用系统通知第一时间提醒你。",
        "System notifications alert you the moment a new proposal card arrives or a task finishes / needs your input.");
    default:
      return text("实时字幕的麦克风转写。", "Live-captions microphone transcription.");
  }
}

/** 一行的动作：请求（壳内 requestPermission——屏幕的首次系统弹窗 / 之后深链由壳决定）或被拒后直接开面板 */
function actFor(kind: Kind, status: PermissionStatus): { method: "requestPermission" | "openPane"; args: Record<string, unknown> } {
  if (status === "denied" && kind === "vault") return { method: "openPane", args: { pane: "files_folders" } };
  if (status === "denied" && (kind === "notifications" || kind === "microphone")) return { method: "openPane", args: { pane: kind } };
  return { method: "requestPermission", args: { kind } };
}

export function CapabilityRows({ onError }: { onError?: (message: string | null) => void }) {
  const { text } = useI18n();
  const shell = useShellState();
  const { permissions } = useAppState();
  const present = hasShellBridge();

  if (!present || !shell) {
    return (
      <p className="settings-warning">
        {text("屏幕录制 / 笔记库 / 通知的真相只有看板 app（壳）自己探得到——请在 app 里打开本页。",
          "Screen Recording / Notes vault / Notifications can only be probed by the board app itself — open this page inside the app.")}
      </p>
    );
  }

  const statusOf = (kind: Kind): PermissionStatus => {
    const own = shell.permissions[kind];
    // 笔记库：壳的被动探针与 server 的被动探针（ingest 链的 mirror 证据）二者任一说 granted 即 granted
    if (kind === "vault" && own !== "granted" && permissions?.vault?.status === "granted") return "granted";
    return own;
  };

  const act = (kind: Kind, status: PermissionStatus) => {
    onError?.(null);
    const { method, args } = actFor(kind, status);
    void callShell(method, args).catch((err) => onError?.(err instanceof Error ? err.message : String(err)));
  };

  return (
    <ul className="settings-list perm-capabilities">
      {ORDER.map((kind) => {
        const status = statusOf(kind);
        const granted = status === "granted";
        return (
          <li key={kind} className="settings-list-row perm-capability" data-kind={kind} data-status={status}>
            <div className="perm-capability-head">
              <span className={`perm-dot is-${granted ? "granted" : status === "denied" ? "denied" : "unknown"}`} aria-hidden="true" />
              <span className="settings-list-title">{rowName(kind, text)}</span>
              <span className={`perm-status is-${granted ? "granted" : status === "denied" ? "denied" : "unknown"}`}>{capabilityStatusText(kind, status, text)}</span>
              <span className="perm-capability-action">
                {granted
                  ? <span className="perm-check" aria-label={text("已授权", "Granted")}>✓</span>
                  : <button type="button" className="btn" onClick={() => act(kind, status)}>{capabilityButtonLabel(kind, status, text, shell.permissions.screen_requested === true)}</button>}
              </span>
            </div>
            <p className="settings-list-desc">{rowWhy(kind, text)}</p>
            {kind === "vault" && permissions?.vault?.root && (
              <p className="settings-list-dim">{text("笔记库根：", "Vault root: ")}<code>{permissions.vault.root}</code></p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
