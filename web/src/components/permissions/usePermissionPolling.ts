// 壳 TCC 探针的 2 s 轮询（原生 PermissionsModel.startPolling 的节拍，Permissions.swift:46；CONTRACT §68.3 / §68.5）：
// 用户正在系统设置里翻开关，页面上的行要自己变绿——权限体检页与首次运行向导（整个向导期间，不只权限步：
// 原生 SetupWizardController.show 一开窗就 perms.startPolling，关窗才停）共用这一把。桥不在场 = no-op
// （浏览器里 GUI 项本就只能如实说「只在看板 app 里可探」）；卸载即清计时器。
import { useEffect } from "react";
import { callShell } from "../../shellBridge";

export const PERMISSION_POLL_MS = 2000;

export function usePermissionPolling(present: boolean) {
  useEffect(() => {
    if (!present) return undefined;
    const tick = () => void callShell("getPermissions").catch(() => undefined);
    tick();
    const timer = window.setInterval(tick, PERMISSION_POLL_MS);
    return () => window.clearInterval(timer);
  }, [present]);
}
