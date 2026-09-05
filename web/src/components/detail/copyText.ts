// 剪贴板写入：优先 async Clipboard API，退回 execCommand（http://127.0.0.1 上
// clipboard API 可用——localhost 算 secure context；execCommand 兜底老 WebView）。
// 看板 / 详情侧栏 / 向导 / 权限页 / 会议回顾的复制入口（复制指令行 / 详情 CopyChip / 复制成稿 / 去授权 复制路径…）
// 都从这走，返回布尔、永不抛：调用方按 true/false 给回执或短注（判例 copyText.fallback.test.ts）。
// 唯一例外：设置页 Slack manifest 复制（SlackSection）仍直连 navigator.clipboard（自带 catch → 错误行），未收编。
export async function copyText(value: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    // 落回 execCommand
  }
  let textarea: HTMLTextAreaElement | null = null;
  try {
    textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    // execCommand 抛（老 WebView 没这个 API）也不许把兜底 textarea 留在页面上
    textarea?.remove();
  }
}
