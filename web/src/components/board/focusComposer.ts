// 「聚焦捕获框」的唯一落点（CONTRACT §54.4 2026-09-05 追记）：rail 的 ⌘L、壳菜单 View ▸ 聚焦捕获框（⌘L）与
// 全局 ⌃⌥Space（§68.13，都推 `quick_capture` 命令）三条入口都汇到这里——原生 AppDelegate.swift `focusCaptureField`
// （openMainWindow + 0.15 s 后 post .focusCaptureField）与 Composer.swift 的接收端（`guard mode == .propose`；
// 「already open → just refocus」）的 web 版。
//   - 落在**潜在任务条头的捕获框**：`.backlog-strip:not(.is-archive) .lane-composer textarea`。§78 把捕获框从
//     退役的提案列头搬到了这条左书立条上（§34 追记 / §78：「⌘L 的『归提案 composer』读作『归潜在任务列的
//     捕获框』」），所以这里认的是那只框、**不是**列里的直跑框——原生「只归提案 composer」这条规矩的实质
//     是「全局快捷键只许记一件事（`detected`），永不替 owner 起跑（`approved`、真花钱）」，落点跟着意图走，
//     不跟着 DOM 顺序走；首份 dashboard.json 还没写出来时看板页没有列也没有条，捕获框住在
//     `BoardMissingState`（§54.1 2026-09-05 追记 (b)，原生 Kanban.emptyState 的 KanbanComposer 同样收
//     .focusCaptureField）——退到 `.shell-board-missing .lane-composer textarea`；两态不会同时在 DOM 里。
//     条被用户手动收起时框不在 DOM 里（捕获框住在展开态里），这一下什么都不做、返回 false——按错地方开跑
//     的代价远高于「再点开条按一次」；旗出厂就是 true 且不持久化（D80.3），刷新即回到能接键的常态；
//   - 光标到末尾（setSelectionRange），不 select() 全选：已有草稿时再按 ⌘L 只是把光标交回去，下一键不许覆盖草稿；
//   - 不在看板页：先在 sessionStorage 留标记再 route.navigate 回看板（D40 起是 pushState 换页、不重载；此前是
//     location.assign 整页导航——标记走 sessionStorage 两种情况都接得住，壳又只推一次命令），BoardPage 挂载时消费标记
//     补上那一下聚焦——原生 ⌘L 只前置窗口不换页，web 多走一步是因为看板之外的页原生里根本没有 composer。
import { buildAppUrl, navigate, readPage } from "../../route";

/** sessionStorage 键：离开非看板页去快速捕获时留下的「到了看板先聚焦 composer」接力棒（一次性，读到即删） */
export const PENDING_FOCUS_KEY = "zai.pendingFocus";
const PENDING_FOCUS_COMPOSER = "composer";

/** 潜在任务条（左书立条）头上的快速捕获框——§78 后 ⌘L 的唯一落点。`:not(.is-archive)` 排掉同类名的
 *  右书立条（永久性完成；它只有搜索框，没有 composer——排掉是为了写死意图，不靠「它现在没有」） */
export const COMPOSER_SELECTOR = ".backlog-strip:not(.is-archive) .lane-composer textarea";

/** 看板页「dashboard.json 不存在」空态里的捕获框（AppShell.BoardMissingState；那一态没有列） */
export const BOARD_MISSING_COMPOSER_SELECTOR = ".shell-board-missing .lane-composer textarea";

/** 把光标放进捕获框（潜在任务条头，缺看板时退到 BoardMissingState 那只），caret 到末尾。
 *  找不到（看板还没渲染 / 条被收起）返回 false——绝不退到运行中列的直跑框。 */
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
