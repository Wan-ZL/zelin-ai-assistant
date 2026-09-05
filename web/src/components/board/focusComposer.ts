// 「聚焦捕获框」的唯一落点（CONTRACT §54.4 2026-09-05 追记）：rail 的 ⌘L、壳菜单 View ▸ 聚焦捕获框（⌘L）与
// 全局 ⌃⌥Space（§68.13，都推 `quick_capture` 命令）三条入口都汇到这里——原生 AppDelegate.swift `focusCaptureField`
// （openMainWindow + 0.15 s 后 post .focusCaptureField）与 Composer.swift 的接收端（`guard mode == .propose`；
// 「already open → just refocus」）的 web 版。
//   - 只归提案列 composer：选第一个 `.board-column .lane-composer textarea`（DOM 顺序 = 列顺序，提案列在运行中列之前；
//     原生的运行中 composer 从不响应 ⌘L——两个框抢焦点会打架）；首份 dashboard.json 还没写出来时看板页没有列，
//     提案 composer 住在 `BoardMissingState`（§54.1 2026-09-05 追记 (b)，原生 Kanban.emptyState 的 KanbanComposer 同样收
//     .focusCaptureField）——退到 `.shell-board-missing .lane-composer textarea`；两态不会同时在 DOM 里；
//   - 光标到末尾（setSelectionRange），不 select() 全选：已有草稿时再按 ⌘L 只是把光标交回去，下一键不许覆盖草稿；
//   - 不在看板页：先在 sessionStorage 留标记再整页导航回看板（route.navigate = location.assign，当前文档随之丢弃，
//     壳又只推一次命令），新文档的 BoardPage 挂载时消费标记补上那一下聚焦——原生 ⌘L 只前置窗口不换页，web 多走一步
//     是因为看板之外的页原生里根本没有 composer。
import { buildAppUrl, navigate, readPage } from "../../route";

/** sessionStorage 键：离开非看板页去快速捕获时留下的「到了看板先聚焦 composer」接力棒（一次性，读到即删） */
export const PENDING_FOCUS_KEY = "zai.pendingFocus";
const PENDING_FOCUS_COMPOSER = "composer";

/** 提案列 composer（第一个列顶输入框） */
export const COMPOSER_SELECTOR = ".board-column .lane-composer textarea";

/** 看板页「dashboard.json 不存在」空态里的提案 composer（AppShell.BoardMissingState；那一态没有列） */
export const BOARD_MISSING_COMPOSER_SELECTOR = ".shell-board-missing .lane-composer textarea";

/** 把光标放进提案列 composer，caret 到末尾。找不到（看板还没渲染）返回 false。 */
export function focusComposerField(root: ParentNode = document): boolean {
  const field = root.querySelector<HTMLTextAreaElement>(COMPOSER_SELECTOR)
    ?? root.querySelector<HTMLTextAreaElement>(BOARD_MISSING_COMPOSER_SELECTOR);
  if (!field) return false;
  field.focus();
  const end = field.value.length;
  field.setSelectionRange(end, end);
  return true;
}

function markPendingFocus(): void {
  try {
    window.sessionStorage.setItem(PENDING_FOCUS_KEY, PENDING_FOCUS_COMPOSER);
  } catch {
    /* sessionStorage 不可用：照样回看板，只是到了不自动聚焦 */
  }
}

/** ⌘L / quick_capture 入口：在看板页直接聚焦；不在看板页 → 留标记、回看板（buildAppUrl 去掉 ?page= / ?card=）。 */
export function focusComposer(): void {
  if (readPage(window.location.search) !== "board") {
    markPendingFocus();
    navigate(buildAppUrl(window.location.href, "board", null));
    return;
  }
  focusComposerField();
}

/** BoardPage（或它在缺文件态的替身 BoardMissingState）挂载时调一次：有接力棒就聚焦 composer 并把它删掉（刷新不重放）。
 *  返回是否消费了标记。 */
export function consumePendingFocus(): boolean {
  let pending: string | null;
  try {
    pending = window.sessionStorage.getItem(PENDING_FOCUS_KEY);
    if (pending !== null) window.sessionStorage.removeItem(PENDING_FOCUS_KEY);
  } catch {
    return false;
  }
  if (pending !== PENDING_FOCUS_COMPOSER) return false;
  focusComposerField();
  return true;
}
