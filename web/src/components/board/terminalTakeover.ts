// 双击卡片 = 在终端接管会话（CONTRACT §54.1 第 11 项 / §68.7，issue #216，owner 拍板 2026-09-04）。
// 卡面没有按钮：单击指令行复制、双击整卡接管（文案「单击复制指令 · 双击在终端接管」）；键盘 Enter
// 仍是「打开详情侧栏」（无障碍语义是看详情，不该触发外部进程）。
//
// 流程：POST /api/terminal {card_id}（命令永远由 server 从投影行推导，客户端只传 card_id）→ server 入队
// state/terminal_queue → 壳经 Apple Events 开终端。回执两句逐字镜像原生 CopyPathLine 的
// launched / launchFailed（已在终端打开 / 打开终端失败，§66 清单 gated）。降级：server 说「这里开不了
// 终端」——非 darwin 501、壳没在跑 503 SHELL_UNAVAILABLE（纯浏览器打开看板且壳未运行）——同一条路：
// 把指令复制到剪贴板 + 一句提示；其它失败红字留着并附 server 原句。没有可接管会话的卡（cmd 为空）
// 双击 no-op（现有 400 语义前移到 UI 判断）。
import { useEffect, useRef, useState } from "react";
import { ApiError, postTerminal } from "../../api";
import { useI18n } from "../../i18n";
import { copyText } from "../detail/copyText";

export interface TakeoverStatus {
  msg: string;
  /** server 原句（失败 / 降级时附在句尾） */
  detail?: string;
  failed: boolean;
}

/** server 说「这里开不了终端」：非 darwin 501 / 壳没在跑 503 SHELL_UNAVAILABLE——同一条降级路（复制指令 + 提示） */
export function isTakeoverUnavailable(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 501 || err.code === "SHELL_UNAVAILABLE");
}

const OPENED_TTL_MS = 3000;
const COPIED_TTL_MS = 6000;

/**
 * 一张卡的接管入口：`takeOver()` 由 CardSurface 的双击调用；`status` 渲染成卡上的一行小字。
 * `cmd` 为空 = 这张卡没有可接管的会话（排队卡 / 提案卡…）→ canTakeOver=false，双击 no-op。
 * 在途中忽略重复双击（三击也只发一次）。
 */
export function useTerminalTakeover(cardId: string, cmd: string | null | undefined) {
  const { text } = useI18n();
  const [status, setStatus] = useState<TakeoverStatus | null>(null);
  const busy = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const show = (next: TakeoverStatus, ttl: number | null) => {
    setStatus(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = ttl === null ? null : setTimeout(() => setStatus(null), ttl);
  };

  const takeOver = async () => {
    if (!cmd || busy.current) return;
    busy.current = true;
    try {
      await postTerminal(cardId);
      show({ msg: text("已在终端打开", "Opened in terminal"), failed: false }, OPENED_TTL_MS);
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e);
      if (isTakeoverUnavailable(e) && (await copyText(cmd))) {
        show({
          msg: text("无法直接打开终端 · 已复制指令，粘贴到终端即可接管", "Cannot open a terminal from here · command copied, paste it in a terminal to take over"),
          detail,
          failed: false,
        }, COPIED_TTL_MS);
      } else {
        show({ msg: text("打开终端失败", "Terminal launch failed"), detail, failed: true }, null);
      }
    } finally {
      busy.current = false;
    }
  };

  return { status, takeOver, canTakeOver: typeof cmd === "string" && cmd.length > 0 };
}
