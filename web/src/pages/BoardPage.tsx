// 看板页（G2/A6）：五列 BoardLanes 装配。页面骨架关注点都在壳层——
// 顶栏/连接状态/离线横幅/整页加载与空态归 AppShell（G7），过滤 chips 归 FilterBar（G4）。
// 列与卡的全部逻辑在 components/board/ 下。
// 滚动模型（D42，§54.4 2026-09-06 追记；原生 Kanban.swift：横向 ScrollView 里每列各一个纵向 ScrollView，
// 多选操作条 `.overlay(alignment: .bottom)`）：`.board-page` 是竖排 flex、吃满 `.shell-main` 的一屏高——
// 上面是只横向滚的列区（BoardLanes = `.board-main`，每列的卡片列表各自纵向滚），下面是横贯看板底部的多选操作条；
// 文档与 `.shell-main` 在看板页都不滚。
import { useEffect } from "react";
import { BoardLanes } from "../components/board/BoardLanes";
import { consumePendingFocus } from "../components/board/focusComposer";
import { SelectionBar } from "../components/board/SelectionBar";

export function BoardPage() {
  // ⌘L / quick_capture 从别的页过来（focusComposer 留下的 sessionStorage 接力棒，§54.4 2026-09-05 追记）：
  // 换页（D40 起 pushState 不重载，本组件随之挂载）后在这里补上那一下聚焦。挂载时 composer 已在 DOM——AppShell
  // 只在看板快照到了才渲染 children，effect 又在子树提交之后才跑。
  useEffect(() => {
    consumePendingFocus();
  }, []);
  return (
    <div className="board-page">
      <BoardLanes />
      {/* §21 多选操作条（selectionMode 才渲染；入口「选择」在 FilterBar）——看板底部横贯全宽，不进列区的横排 */}
      <SelectionBar />
    </div>
  );
}
